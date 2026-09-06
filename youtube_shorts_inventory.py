"""Read a complete Studio inventory from provider-owned row models, without edits."""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from pathlib import Path

CAPTURE_JS = r'''
let p=null;
const waitRows=async previous=>{const end=Date.now()+30000;while(Date.now()<end){try{const id=await p.evaluate(()=>document.querySelector('ytcp-video-row')?.video?.videoId||'');if(id&&id!==previous)return;}catch(e){if(!/context/i.test(String(e)))throw e;}await sleep(250);}throw new Error('inventory rows did not become ready');};
try{
 p=await openTab(payload.list_url);await waitRows('');
 const rows=[],pages=[],seen=new Set();
 for(let page=1;page<=80;page++){
  const values=await p.evaluate(({page,channelId})=>{
   if(location.hostname!=='studio.youtube.com')throw new Error('wrong host');
   return [...document.querySelectorAll('ytcp-video-row')].map(e=>{
    const v=e.video,titleNode=e.querySelector('#video-title');
    if(!v||v.channelId!==channelId||typeof v.description!=='string'||!titleNode)throw new Error('provider row model incomplete');
    const title=(titleNode.getAttribute('title')||titleNode.innerText||'').trim();
    const hrefs=[...e.querySelectorAll('a[href]')].map(a=>a.href);
    const identityLinks=[...hrefs,...[...e.querySelectorAll('img')].map(x=>x.src)];
    if(v.title.trim()!==title||!identityLinks.some(x=>x.includes(v.videoId)))throw new Error(JSON.stringify({reason:'provider model differs from visible row',id:v.videoId,titleExact:v.title.trim()===title,idExact:identityLinks.some(x=>x.includes(v.videoId)),title,modelTitle:v.title}));
    const lines=(e.innerText||'').split('\n').map(s=>s.trim());
    const patterns={public:/^(공개|Public)$/i,scheduled:/^(예약됨|Scheduled)$/i,private:/^(비공개|Private)$/i,draft:/^(초안|Draft)$/i};
    const states=Object.entries(patterns).filter(([k,re])=>lines.some(x=>re.test(x))).map(([k])=>k);
    if(states.length!==1)throw new Error('ambiguous visibility');
    return {identity:v.videoId,provider_id:v.videoId,status:states[0],title,description:v.description,urls:[...hrefs,...(v.description.match(/https?:\/\/[^\s]+/g)||[])],page,
     metadata_origin:'studio_provider_row_model',model_channel_id:v.channelId,privacy:v.privacy,draft_status:v.draftStatus,
     scheduled_raw:v.scheduledPublishingDetails,published_seconds:v.timePublishedSeconds,
     direct_metadata_inspected:true,visibility_control_present:null};
   });
  },{page,channelId:payload.channel_id});
  if(!values.length)throw new Error('empty page');
  for(const row of values){if(seen.has(row.identity))throw new Error('duplicate pagination row');seen.add(row.identity);rows.push(row);}
  const next=p.locator('#navigate-after,ytcp-icon-button#navigate-after');
  if(await next.count()!==1)throw new Error('pagination control cardinality');
  const disabled=(await next.getAttribute('aria-disabled'))==='true'||(await next.getAttribute('disabled'))!==null||!await next.isEnabled();
  pages.push({page,row_count:values.length,next_disabled:disabled});
  if(disabled)break;
  await next.click();await waitRows(values[0].identity);
 }
 if(!pages.at(-1)?.next_disabled)throw new Error('pagination not complete');
 emit({status:'pass',rows,pages,captured_at:new Date().toISOString(),scan_id:payload.scan_id,channel_id:payload.channel_id});
}catch(e){emit({status:'blocked',error:String(e?.message||e)});}finally{try{if(p)await p.close();}catch(_){}}
'''


def capture(root: Path) -> dict:
    from aside_browser import JS_COMMON, _payload_expression, run_repl
    from youtube_shorts_aside_adapter import CHANNEL_ID, STUDIO_SHORTS_URL
    payload = {'list_url': STUDIO_SHORTS_URL, 'channel_id': CHANNEL_ID, 'scan_id': uuid.uuid4().hex}
    result = run_repl(JS_COMMON + '\nconst payload=' + _payload_expression(payload) + ';\n' + CAPTURE_JS,
                      account='u0', timeout=150, cwd=root)
    (root / f'inventory-provider-model-{payload["scan_id"]}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def normalize(raw: dict, manifest, phase: str) -> dict:
    from youtube_shorts_aside_adapter import CHANNEL_ID, KST
    from youtube_shorts_publisher import InventoryError, STATES
    if raw.get('status') != 'pass' or raw.get('channel_id') != CHANNEL_ID:
        raise InventoryError('Studio provider model capture failed: ' + str(raw.get('error')))
    pages = raw.get('pages', [])
    if not pages or pages[-1].get('next_disabled') is not True:
        raise InventoryError('Studio provider model pagination is incomplete')
    rows = []
    for source in raw['rows']:
        row = dict(source)
        if row.get('model_channel_id') != CHANNEL_ID or row.get('metadata_origin') != 'studio_provider_row_model':
            raise InventoryError('provider model channel or origin differs')
        status = row['status']
        scheduled = [x for x in (row.get('scheduled_raw') or {}).get('scheduledPublishings', [])
                     if x.get('status') == 'SCHEDULED_PUBLISHING_STATUS_SCHEDULED']
        if status == 'scheduled':
            if len(scheduled) != 1 or scheduled[0].get('action') != 'SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC':
                raise InventoryError('provider scheduled publishing is ambiguous')
            row['scheduled_at'] = datetime.fromtimestamp(int(scheduled[0]['scheduledTimeSeconds']), KST).isoformat()
        elif scheduled:
            raise InventoryError('provider schedule differs from visible status')
        if status == 'public':
            if row['privacy'] != 'VIDEO_PRIVACY_PUBLIC' or int(row['published_seconds']) <= 0:
                raise InventoryError('provider public status differs')
            row['published_at'] = datetime.fromtimestamp(int(row['published_seconds']), KST).isoformat()
        elif row['privacy'] != 'VIDEO_PRIVACY_PRIVATE':
            raise InventoryError('provider private status differs')
        if (status == 'draft') != (row['draft_status'] != 'DRAFT_STATUS_NONE'):
            raise InventoryError('provider draft state differs')
        rows.append(row)
    captured = datetime.fromisoformat(raw['captured_at'].replace('Z', '+00:00')).astimezone(KST).isoformat()
    return {'status':'pass','scan_id':raw['scan_id'],'phase':phase,'captured_at':captured,
            'account':'u0','headless':True,'channel':manifest.expected_channel,
            'pagination_complete':True,'terminal_reason':'next_disabled','pages_scanned':len(pages),
            'pages':pages,'scanned_states':sorted(STATES),
            'status_counts':{state:sum(r['status']==state for r in rows) for state in STATES},'rows':rows}


def scan(manifest, *, phase: str) -> dict:
    """Read two complete snapshots; any identity, text or schedule drift blocks."""
    from youtube_shorts_aside_adapter import _kst_now
    from youtube_shorts_publisher import ProviderInventory, InventoryError
    first = normalize(capture(manifest.video.parent), manifest, phase)
    ProviderInventory.from_mapping(first, manifest=manifest, now=_kst_now(), phase=phase)
    second = normalize(capture(manifest.video.parent), manifest, phase)
    ProviderInventory.from_mapping(first, manifest=manifest, now=_kst_now(), phase=phase)
    ProviderInventory.from_mapping(second, manifest=manifest, now=_kst_now(), phase=phase)
    if first['rows'] != second['rows'] or first['pages'] != second['pages']:
        raise InventoryError('Studio provider model changed between complete snapshots')
    second['metadata_capture_method'] = 'two_matching_provider_row_model_snapshots'
    return second
