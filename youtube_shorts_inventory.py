"""Read a complete Studio inventory from provider-owned row models, without edits."""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from pathlib import Path

CAPTURE_JS = r'''
let p=null;
const waitRows=async previous=>{const end=Date.now()+30000;while(Date.now()<end){try{const id=await p.evaluate(()=>document.querySelector('ytcp-video-row')?.video?.videoId||'');if(id&&id!==previous)return;}catch(e){if(!/context/i.test(String(e)))throw e;}await sleep(250);}throw new Error('inventory rows did not become ready');};
let channelSelectionClicks=0;
const recoverExactTargetChannel=async()=>{
 await sleep(3000);
 const denied=await p.evaluate(()=>/페이지를 볼 권한이 없습니다|don't have permission|do not have permission/i.test(document.body?.innerText||''));
 if(!denied)return;
 await p.goto(payload.channel_switcher_url);await sleep(3500);
 const count=await p.evaluate(({name,handle})=>{let count=0;for(const card of document.querySelectorAll('ytd-account-item-renderer')){const title=(card.querySelector('#channel-title')?.textContent||'').replace(/\s+/g,' ').trim();const lines=(card.innerText||'').split('\n').map(x=>x.trim());if(title===name&&lines.includes(handle)){card.setAttribute('data-exact-inventory-channel','true');count++;}}return count;},{name:payload.channel_name,handle:payload.channel_handle});
 if(count!==1)throw new Error('exact target channel unavailable:'+count);
 await p.locator('ytd-account-item-renderer[data-exact-inventory-channel="true"]').click();channelSelectionClicks=1;await sleep(3500);
 await p.goto(payload.list_url);await sleep(3000);
 const stillDenied=await p.evaluate(()=>/페이지를 볼 권한이 없습니다|don't have permission|do not have permission/i.test(document.body?.innerText||''));
 if(stillDenied)throw new Error('exact target channel remains unavailable after one selection');
};
try{
 p=await openTab(payload.list_url);await recoverExactTargetChannel();await waitRows('');
 const rows=[],pages=[],seen=new Set();
 for(let page=1;page<=80;page++){
  const pageSnapshot=await p.evaluate(({page,channelId})=>{
   if(location.hostname!=='studio.youtube.com')throw new Error('wrong host');
   const values=[...document.querySelectorAll('ytcp-video-row')].map(e=>{
    const v=e.video,titleNode=e.querySelector('#video-title');
    if(!v||v.channelId!==channelId||typeof v.description!=='string'||!titleNode)throw new Error('provider row model incomplete');
    const title=(titleNode.getAttribute('title')||titleNode.innerText||'').trim();
    const hrefs=[...e.querySelectorAll('a[href]')].map(a=>a.href);
    const identityLinks=[...hrefs,...[...e.querySelectorAll('img')].map(x=>x.src)];
    if(v.title.trim()!==title||!identityLinks.some(x=>x.includes(v.videoId)))throw new Error(JSON.stringify({reason:'provider model differs from visible row',id:v.videoId,titleExact:v.title.trim()===title,idExact:identityLinks.some(x=>x.includes(v.videoId)),title,modelTitle:v.title}));
    const lines=(e.innerText||'').split('\n').map(s=>s.trim());
    const patterns={public:/^(공개|Public)$/i,scheduled:/^(예약됨|Scheduled)$/i,private:/^(비공개|Private)$/i,draft:/^(초안|Draft)$/i};
    const states=Object.entries(patterns).filter(([k,re])=>lines.some(x=>re.test(x))).map(([k])=>k);
    // A row that is still uploading, processing or queued for review has no
    // visibility chip yet, and the wording changes as it moves through those
    // phases. Read the provider's own draft status instead of chasing the text:
    // a draft with no chip rendered has simply not surfaced one yet. Naming that
    // stops one in-flight upload from making every scan unreadable, and a row
    // with no state and no draft status still fails.
    const notYetVisible=v.draftStatus&&v.draftStatus!=='DRAFT_STATUS_NONE';
    if(states.length===0&&notYetVisible)return {identity:v.videoId,provider_id:v.videoId,status:'uploading',title,description:v.description,urls:[...hrefs,...(v.description.match(/https?:\/\/[^\s]+/g)||[])],page,
     metadata_origin:'studio_provider_row_model',model_channel_id:v.channelId,privacy:v.privacy,draft_status:v.draftStatus,
     scheduled_raw:v.scheduledPublishingDetails,published_seconds:v.timePublishedSeconds,
     direct_metadata_inspected:true,visibility_control_present:null};
    if(states.length!==1)throw new Error('ambiguous visibility '+JSON.stringify({id:v.videoId,title,states,lines:lines.slice(0,20)}));
    return {identity:v.videoId,provider_id:v.videoId,status:states[0],title,description:v.description,urls:[...hrefs,...(v.description.match(/https?:\/\/[^\s]+/g)||[])],page,
     metadata_origin:'studio_provider_row_model',model_channel_id:v.channelId,privacy:v.privacy,draft_status:v.draftStatus,
     scheduled_raw:v.scheduledPublishingDetails,published_seconds:v.timePublishedSeconds,
     direct_metadata_inspected:true,visibility_control_present:null};
   });
   const nextControls=[...document.querySelectorAll('#navigate-after,ytcp-icon-button#navigate-after')];
   const next=nextControls[0]||null;
   return {values,next_count:nextControls.length,
    next_aria_disabled:next?.getAttribute('aria-disabled')||null,
    next_disabled_attribute:next?.getAttribute('disabled')};
  },{page,channelId:payload.channel_id});
  const values=pageSnapshot.values;
  if(!values.length)throw new Error('empty page');
  for(const row of values){if(seen.has(row.identity))throw new Error('duplicate pagination row');seen.add(row.identity);rows.push(row);}
  const next=p.locator('#navigate-after,ytcp-icon-button#navigate-after');
  if(pageSnapshot.next_count!==1)throw new Error('pagination control cardinality');
  const disabled=pageSnapshot.next_aria_disabled==='true'||pageSnapshot.next_disabled_attribute!==null||!await next.isEnabled();
  pages.push({page,row_count:values.length,next_disabled:disabled});
  if(disabled)break;
  await next.click();await waitRows(values[0].identity);
 }
 if(!pages.at(-1)?.next_disabled)throw new Error('pagination not complete');
 emit({status:'pass',rows,pages,captured_at:new Date().toISOString(),scan_id:payload.scan_id,channel_id:payload.channel_id,provider_observed_channel_selection_click_count:channelSelectionClicks});
}catch(e){emit({status:'blocked',error:String(e?.message||e),provider_observed_channel_selection_click_count:channelSelectionClicks});}finally{try{if(p)await p.close();}catch(_){}}
'''

MAX_STABILITY_CAPTURES = 4


def capture(root: Path) -> dict:
    from aside_browser import JS_COMMON, _payload_expression, run_repl
    from youtube_shorts_aside_adapter import CHANNEL_ID, STUDIO_SHORTS_URL
    payload = {'list_url': STUDIO_SHORTS_URL, 'channel_id': CHANNEL_ID, 'scan_id': uuid.uuid4().hex,
               'channel_switcher_url': 'https://www.youtube.com/channel_switcher',
               'channel_name': '나민수 AI', 'channel_handle': '@naminsoo_aimax'}
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
        # A row still uploading is a draft that has not finished transferring, so
        # the provider marks it with a draft status just like a finished draft.
        if (status in ('draft', 'uploading')) != (row['draft_status'] != 'DRAFT_STATUS_NONE'):
            raise InventoryError('provider draft state differs')
        rows.append(row)
    captured = datetime.fromisoformat(raw['captured_at'].replace('Z', '+00:00')).astimezone(KST).isoformat()
    return {'status':'pass','scan_id':raw['scan_id'],'phase':phase,'captured_at':captured,
            'account':'u0','headless':True,'channel':manifest.expected_channel,
            'pagination_complete':True,'terminal_reason':'next_disabled','pages_scanned':len(pages),
            'pages':pages,'scanned_states':sorted(STATES),
            'status_counts':{state:sum(r['status']==state for r in rows) for state in STATES},'uploading_rows':sum(r['status']=='uploading' for r in rows),'rows':rows}


def scan(manifest, *, phase: str) -> dict:
    """Require two consecutive complete, validated snapshots to match."""
    from youtube_shorts_aside_adapter import _kst_now
    from youtube_shorts_publisher import ProviderInventory, InventoryError
    previous = None
    for _ in range(MAX_STABILITY_CAPTURES):
        current = normalize(capture(manifest.video.parent), manifest, phase)
        ProviderInventory.from_mapping(current, manifest=manifest, now=_kst_now(), phase=phase)
        if (previous is not None
                and previous['rows'] == current['rows']
                and previous['pages'] == current['pages']):
            current['metadata_capture_method'] = 'two_matching_provider_row_model_snapshots'
            return current
        previous = current
    raise InventoryError(
        'Studio provider model did not stabilize in '
        f'{MAX_STABILITY_CAPTURES} complete snapshots'
    )
