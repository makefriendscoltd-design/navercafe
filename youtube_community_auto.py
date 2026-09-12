"""Fill or publish a YouTube Community post through Aside CLI.

The default is intentionally non-publishing: it fills the composer and leaves
the final Post button untouched.  ``--publish`` is required for the external
write, and ``--expected-channel`` should be supplied with it.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import json
import hashlib
import fcntl
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aside_browser import (
    JS_COMMON,
    _embedded_uploads,
    _payload_expression,
    post_to_youtube_community,
    run_repl,
    staged_uploads,
)
from youtube_community_verification import inspect_posts
from external_publish_tracking import track_external_event, track_publication


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.ini"
KST = ZoneInfo("Asia/Seoul")
PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")


def _save_receipt(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _parse_schedule_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--schedule-at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--schedule-at requires an explicit Asia/Seoul offset")
    local = parsed.astimezone(KST)
    if parsed.utcoffset() != local.utcoffset():
        raise ValueError("--schedule-at must use the Asia/Seoul offset")
    if local <= datetime.now(KST):
        raise ValueError("--schedule-at must be in the future")
    return local


def _channel_id(community_url: str) -> str:
    match = re.search(r"/channel/([A-Za-z0-9_-]+)", community_url)
    if not match:
        raise ValueError("Scheduling requires --community-url with an exact /channel/<id>/posts URL")
    return match.group(1)


def _schedule_inventory(*, channel_id: str, expected_channel: str,
                        community_url: str, account=None) -> dict:
    payload = {
        "channel": expected_channel,
        "scheduledUrl": f"https://www.youtube.com/channel/{channel_id}/posts?pvf=CAE%253D",
        "publicUrl": community_url.split("?", 1)[0],
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r'''
async function scan(p,scheduled){
  await sleep(2600);
  if(scheduled){
    // The 게시됨/예약됨/보관처리됨 strip lives in ytd-post-stream-filter-renderer's
    // shadow root, so a light-DOM query never finds it and every scheduled read
    // came back as a view mismatch.
    const found=await p.evaluate(()=>{const all=[];
      const walk=root=>{for(const el of root.querySelectorAll('tp-yt-paper-tab,[role=tab],*')){
        if(el.tagName==='TP-YT-PAPER-TAB'||el.getAttribute('role')==='tab')all.push(el);
        if(el.shadowRoot)walk(el.shadowRoot);}};
      walk(document);
      const e=all.find(x=>/^(예약됨|Scheduled)$/.test((x.innerText||'').trim()));if(!e)return false;
      if(e.getAttribute('aria-selected')!=='true'&&!e.classList.contains('iron-selected'))e.click();return true;});
    if(!found)return {status:'view_mismatch',rows:[],exhausted:false};
    await sleep(1800);
  }
  let stable=0,last=-1,exhausted=false;
  for(let i=0;i<60;i++){
    const state=await p.evaluate(()=>({count:document.querySelectorAll('ytd-backstage-post-thread-renderer').length,
      continuation:!!document.querySelector('ytd-continuation-item-renderer')}));
    await p.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await sleep(550);
    if(state.count===last)stable++;else stable=0;last=state.count;
    if(!state.continuation&&stable>=2){exhausted=true;break;}
  }
  const rows=await p.evaluate(()=>[...document.querySelectorAll('ytd-backstage-post-thread-renderer')]
    .map(node=>{const restore=run=>{const shown=String(run?.text||'');const raw=run?.navigationEndpoint?.urlEndpoint?.url||run?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url||'';if(!raw||!shown.includes('...'))return shown;try{const u=new URL(raw,'https://www.youtube.com');return u.searchParams.get('q')||u.searchParams.get('url')||raw;}catch(_){return raw;}};const renderer=node.querySelector('ytd-backstage-post-renderer')||node,d=renderer.data||node.data||{},link=node.querySelector('#published-time-text a[href*="/post/"]'),runs=d.contentText?.runs||[],text=runs.length?runs.map(restore).join(''):(node.querySelector('#content-text')?.innerText||'');return {post_id:d.postId||(link?.getAttribute('href')||'').split('/post/')[1]||'',url:link?.href||'',text,time:(link?.innerText||node.querySelector('#published-time-text')?.innerText||'').trim(),scheduled_epoch:d.scheduledPublishTimeSec?Number(d.scheduledPublishTimeSec):null,image_count:d.backstageAttachment?.postMultiImageRenderer?.images?.length||0};}));
  return {status:'ok',rows,row_count:rows.length,exhausted};
}
let p=await openTab(payload.scheduledUrl);
try{
  const loggedOut=await p.evaluate(()=>/accounts\.google\.com|ServiceLogin/i.test(location.href));
  if(loggedOut)throw new Error('youtube-login-required');
  const identity=await p.evaluate(()=>({title:document.title,body:(document.body?.innerText||'').slice(0,5000)}));
  if(!identity.title.includes(payload.channel)&&!identity.body.includes(payload.channel))throw new Error('channel-mismatch');
  const scheduled=await scan(p,true);
  await p.goto(payload.publicUrl,{waitUntil:'domcontentloaded'});
  const published=await scan(p,false);
  emit({status:scheduled.status==='ok'&&published.status==='ok'&&scheduled.exhausted&&published.exhausted?'ok':'incomplete',
    channel:payload.channel,scheduled,public:published});
}catch(error){emit({status:'error',error:String(error?.message||error)});}
finally{try{await p.close();}catch(_){}}
'''
    result = run_repl(code, timeout=240, account=account)
    if result.get("status") != "ok":
        raise RuntimeError(f"Community inventory is incomplete: {result}")
    return result


def _inventory_times(inventory: dict) -> list[datetime]:
    values = []
    for row in inventory["scheduled"]["rows"]:
        raw = row.get("scheduled_epoch")
        if raw is None:
            match = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})", row.get("time", ""))
            if not match:
                raise RuntimeError("Scheduled inventory contains an unparseable time")
            year, month, day, hour, minute = map(int, match.groups())
            values.append(datetime(year, month, day, hour, minute, tzinfo=KST))
        else:
            values.append(datetime.fromtimestamp(int(raw), KST))
    return values


def _validate_community_slot(slot: datetime, inventory: dict) -> dict:
    existing = _inventory_times(inventory)
    checks = {
        "future": slot > datetime.now(KST),
        "preferred_hour": slot.hour in (11, 20) and slot.minute == 0 and slot.second == 0,
        "slot_open": slot not in existing,
        "daily_count_below_two": sum(value.date() == slot.date() for value in existing) < 2,
        "minimum_five_hour_gap": all(abs((slot - value).total_seconds()) >= 5 * 3600 for value in existing),
        "same_day_public_guard": slot.date() != datetime.now(KST).date() or not inventory["public"]["rows"],
    }
    if not all(checks.values()):
        raise RuntimeError(f"Community schedule policy failed: {checks}")
    return {"checks": checks, "scheduled_times": [value.isoformat() for value in sorted(existing)]}


def _next_community_slot(inventory: dict) -> datetime:
    now = datetime.now(KST)
    for offset in range(0, 61):
        day = (now + timedelta(days=offset)).date()
        for hour in (11, 20):
            candidate = datetime(day.year, day.month, day.day, hour, 0, tzinfo=KST)
            try:
                _validate_community_slot(candidate, inventory)
            except RuntimeError:
                continue
            return candidate
    raise RuntimeError("No policy-compliant Community slot is available within 60 days")


def _schedule_provider(text: str, images: list[str], *, source_key: str, source_url: str, slot: datetime,
                       channel_id: str, expected_channel: str, account=None) -> dict:
    date_ui = slot.strftime("%b") + f" {slot.day}, {slot.year}"
    hour12 = slot.hour % 12 or 12
    time_ui = f"{hour12}:{slot.minute:02d} {'AM' if slot.hour < 12 else 'PM'}"
    scheduled_text = f"{slot.year}. {slot.month}. {slot.day}. {slot.hour}:{slot.minute:02d} 예정(현지 시간)"
    with staged_uploads(images) as (upload_dir, upload_names):
        payload = {
            "text": text,
            "images": _embedded_uploads(upload_dir, upload_names),
            "source": source_key,
            "sourceUrl": source_url,
            "channel": expected_channel,
            "date": date_ui,
            "time": time_ui,
            "scheduledText": scheduled_text,
            "scheduledUrl": f"https://www.youtube.com/channel/{channel_id}/posts?pvf=CAE%253D",
            "composerUrl": f"https://www.youtube.com/channel/{channel_id}/posts?show_create_dialog=1",
            "slot": slot.isoformat(),
        }
        code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r'''
const normalize=s=>(s||'').replace(/\r/g,'').replace(/\n{2,}/g,'\n\n').trim();
let clicks=0,p=null;
try{
  p=await openTab(payload.scheduledUrl);await sleep(2800);
  if(await p.evaluate(()=>/accounts\.google\.com|ServiceLogin/i.test(location.href)))throw new Error('youtube-login-required');
  const identity=await p.evaluate(()=>({title:document.title,body:(document.body?.innerText||'').slice(0,5000)}));
  if(!identity.title.includes(payload.channel)&&!identity.body.includes(payload.channel))throw new Error('channel-mismatch');
  const tabFound=await p.evaluate(()=>{const e=[...document.querySelectorAll('tp-yt-paper-tab,[role=tab]')].find(x=>/^(예약됨|Scheduled)$/.test((x.innerText||'').trim()));if(!e)return false;if(e.getAttribute('aria-selected')!=='true'&&!e.classList.contains('iron-selected'))e.click();return true;});
  if(!tabFound)throw new Error('scheduled-tab-missing');await sleep(1800);
  const duplicate=await p.evaluate(({text,source})=>[...document.querySelectorAll('ytd-backstage-post-thread-renderer')].filter(node=>{const d=(node.querySelector('ytd-backstage-post-renderer')||node).data||node.data||{};const runs=d.contentText?.runs||[];const body=runs.length?runs.map(run=>{const shown=String(run?.text||'');const raw=run?.navigationEndpoint?.urlEndpoint?.url||run?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url||'';if(!raw||!shown.includes('...'))return shown;try{const u=new URL(raw,'https://www.youtube.com');return u.searchParams.get('q')||u.searchParams.get('url')||raw;}catch(_){return raw;}}).join(''):(node.querySelector('#content-text')?.innerText||'');return body.trim()===text.trim()||body.includes(source);}).length,{text:payload.text,source:payload.source});
  if(duplicate)throw new Error(`scheduled-duplicate:${duplicate}`);
  await p.goto(payload.composerUrl,{waitUntil:'domcontentloaded'});await sleep(2800);
  let box=await waitForContext(p,'#contenteditable-root[contenteditable="true"],[contenteditable="true"][aria-label*="공유해 보세요"]',16000);
  if(!box){await p.evaluate(()=>{const all=[...document.querySelectorAll('button,[role=button]')];const b=all.find(x=>x.id==='commentbox-placeholder')||all.find(x=>x.matches?.('button.ytd-backstage-post-dialog-renderer'))||all.find(x=>/어떤 생각을|새로운 소식을|공유해 보세요|무엇을|게시물 작성/.test((x.innerText||x.getAttribute('aria-label')||'')));if(b)b.click();});await sleep(500);box=await waitForContext(p,'#contenteditable-root[contenteditable="true"],[contenteditable="true"][aria-label*="공유해 보세요"]',8000);}
  if(!box)throw new Error('composer-missing');
  const composerChannel=(await p.locator('ytd-backstage-post-dialog-renderer[is-open] #header-channel-name').first().innerText()).trim();
  if(composerChannel!==payload.channel)throw new Error(`composer-channel-mismatch:${composerChannel}`);
  const tag=await box.loc.evaluate(el=>el.tagName).catch(()=> '');
  if(tag==='TEXTAREA'||tag==='INPUT')await box.loc.fill(payload.text);
  else{await box.loc.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await box.ctx.evaluate(value=>document.execCommand('insertText',false,value),payload.text);}
  const imageMode=await p.evaluate(()=>{const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const b=[...(root?.querySelectorAll('button,[role=button]')||[])].find(x=>/^(이미지|사진|Image|Photo)$/.test((x.innerText||x.getAttribute('aria-label')||'').trim())&&shown(x));if(!b)return false;b.click();return true;});
  if(!imageMode)throw new Error('image-mode-missing');await sleep(700);
  let input=null,fallback=null;
  for(const ctx of await contextsFor(p)){const inputs=ctx.locator('ytd-backstage-post-dialog-renderer[is-open] input[type="file"][name="Filedata"][multiple]');for(let i=0;i<await inputs.count();i++){const item=inputs.nth(i);fallback={ctx,loc:item};const active=await item.evaluate(el=>{const owner=el.closest('ytd-backstage-multi-image-select-renderer,ytd-backstage-image-select-renderer,tp-yt-paper-dialog');if(!owner)return false;const r=owner.getBoundingClientRect(),s=getComputedStyle(owner);return !owner.hidden&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';});if(active){input={ctx,loc:item};break;}}if(input)break;}input=input||fallback;
  if(!input)throw new Error('image-input-missing');
  await input.loc.setInputFiles(payload.images.map(item=>({name:item.name,mimeType:item.mimeType,buffer:Buffer.from(item.base64,'base64')})));
  let attached=0,visual=false;const uploadEnd=Date.now()+45000;
  while(Date.now()<uploadEnd){const state=await p.evaluate(()=>{const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');const multi=[...(root?.querySelectorAll('ytd-backstage-multi-image-select-renderer')||[])].find(el=>!el.hidden&&el.showImagesPreview);const shown=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};return {count:Number(multi?.images?.length||0),visual:!!multi&&[...multi.querySelectorAll('img')].some(shown)};});attached=state.count;visual=state.visual;if(attached===10&&visual)break;await sleep(500);}
  const landed=(await p.locator('#contenteditable-root').first().innerText()).trim();
  if(attached!==10||!visual||normalize(landed)!==normalize(payload.text)||!landed.includes(payload.sourceUrl))throw new Error(`fill-mismatch:${attached}:${visual}`);
  const menu=await p.evaluate(()=>{const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');const b=[...root.querySelectorAll('button,[role=button]')].find(e=>/^(작업 메뉴|Action menu)$/.test(e.getAttribute('aria-label')||''));if(!b)return false;b.click();return true;});await sleep(400);
  const opened=menu&&await p.evaluate(()=>{const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const e=[...document.querySelectorAll('ytd-menu-service-item-renderer,tp-yt-paper-item,[role=menuitem]')].find(x=>/^(게시물 예약|Schedule post)$/.test((x.innerText||'').trim())&&shown(x));if(!e)return false;e.click();return true;});
  if(!opened)throw new Error('schedule-menu-missing');await sleep(800);
  await p.locator('ytd-date-time-picker-renderer ytd-calendar-date-picker #date-picker').click();await sleep(250);
  const dateInput=p.locator('ytd-date-time-picker-renderer ytd-calendar-date-picker input#textbox');await dateInput.fill(payload.date);await dateInput.press('Enter');await sleep(500);
  await p.locator('ytd-date-time-picker-renderer #time-picker').click();await sleep(250);
  const timeSet=await p.evaluate(time=>{const list=document.querySelector('ytd-date-time-picker-renderer #time-listbox');const x=[...list.querySelectorAll('tp-yt-paper-item')].find(e=>(e.innerText||'').replace(/\s/g,'')===time.replace(/\s/g,''));if(!x)return false;x.click();return true;},payload.time);await sleep(500);
  const ready=await p.evaluate(()=>({channel:(document.querySelector('ytd-backstage-post-dialog-renderer[is-open] #header-channel-name')?.innerText||'').trim(),date:document.querySelector('ytd-date-time-picker-renderer ytd-calendar-date-picker #date-label-text')?.innerText||'',time:document.querySelector('ytd-date-time-picker-renderer #time-label-text')?.innerText||'',timezone:document.querySelector('ytd-date-time-picker-renderer #timezone-label-text')?.innerText||'',images:Number(document.querySelector('ytd-backstage-multi-image-select-renderer')?.images?.length||0),button:[...document.querySelectorAll('ytd-backstage-post-dialog-renderer[is-open] button')].find(b=>/^(예약|Schedule)$/.test((b.innerText||'').trim()))?.innerText||''}));
  if(!timeSet||ready.channel!==payload.channel||ready.date!==payload.date||ready.time.replace(/\s/g,'')!==payload.time.replace(/\s/g,'')||!ready.timezone.includes('GMT+0900')||ready.images!==10||!/^(예약|Schedule)$/.test(ready.button))throw new Error('schedule-precommit-mismatch:'+JSON.stringify(ready));
  const precommit=Buffer.from(await p.screenshot()).toString('base64');
  const clicked=await p.evaluate(()=>{const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');const b=[...root.querySelectorAll('button,[role=button]')].find(x=>/^(예약|Schedule)$/.test((x.innerText||'').trim()));if(!b||b.disabled||b.getAttribute('aria-disabled')==='true')return false;b.click();return true;});
  if(!clicked)throw new Error('schedule-button-not-ready');clicks=1;
  let confirmed=false,toast='';const end=Date.now()+20000;
  while(Date.now()<end&&!confirmed){await sleep(700);const state=await p.evaluate(()=>({composer:!!document.querySelector('ytd-backstage-post-dialog-renderer[is-open]'),texts:[...document.querySelectorAll('tp-yt-paper-toast,ytd-notification-action-renderer,[role=status]')].map(e=>(e.innerText||'').trim()).filter(Boolean)}));toast=state.texts.find(x=>/게시물이 예약되었습니다|Post scheduled/i.test(x))||toast;confirmed=!state.composer||!!toast;}
  if(!confirmed)throw new Error('schedule-confirmation-missing');
  await p.goto(payload.scheduledUrl,{waitUntil:'domcontentloaded'});await sleep(2800);
  const verify=async reload=>{if(reload){await p.reload({waitUntil:'domcontentloaded'});await sleep(2800);}const found=await p.evaluate(()=>{const e=[...document.querySelectorAll('tp-yt-paper-tab,[role=tab]')].find(x=>/^(예약됨|Scheduled)$/.test((x.innerText||'').trim()));if(!e)return false;if(e.getAttribute('aria-selected')!=='true'&&!e.classList.contains('iron-selected'))e.click();return true;});await sleep(1800);let stable=0,last=-1,exhausted=false;for(let i=0;i<60;i++){const state=await p.evaluate(()=>({count:document.querySelectorAll('ytd-backstage-post-thread-renderer').length,continuation:!!document.querySelector('ytd-continuation-item-renderer')}));await p.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await sleep(550);if(state.count===last)stable++;else stable=0;last=state.count;if(!state.continuation&&stable>=2){exhausted=true;break;}}const result=await p.evaluate(({text,source,sourceUrl,found})=>{const restore=run=>{const shown=String(run?.text||'');const raw=run?.navigationEndpoint?.urlEndpoint?.url||run?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url||'';if(!raw||!shown.includes('...'))return shown;try{const u=new URL(raw,'https://www.youtube.com');return u.searchParams.get('q')||u.searchParams.get('url')||raw;}catch(_){return raw;}};const rows=[...document.querySelectorAll('ytd-backstage-post-thread-renderer')].map(node=>{const renderer=node.querySelector('ytd-backstage-post-renderer')||node,d=renderer.data||node.data||{},link=node.querySelector('#published-time-text a[href*="/post/"]'),runs=d.contentText?.runs||[],body=runs.length?runs.map(restore).join(''):(node.querySelector('#content-text')?.innerText||'');return {body,post_id:d.postId||(link?.getAttribute('href')||'').split('/post/')[1]||'',url:link?.href||'',time:(link?.innerText||'').trim(),images:d.backstageAttachment?.postMultiImageRenderer?.images?.length||0};});const matches=rows.filter(row=>row.body.trim()===text.trim()&&row.body.includes(source)&&row.body.includes(sourceUrl));return {tab_found:found,matches,row_count:rows.length};},{text:payload.text,source:payload.source,sourceUrl:payload.sourceUrl,found});return {...result,exhausted};};
  const first=await verify(false),reloaded=await verify(true);
  if(!first.exhausted||!reloaded.exhausted||first.matches.length>1||reloaded.matches.length!==1)throw new Error(`scheduled-exact-match-count:${first.matches.length}:${reloaded.matches.length}:${first.exhausted}:${reloaded.exhausted}`);
  const b=reloaded.matches[0],a=first.matches[0]||b;
  if(!a.post_id||a.post_id!==b.post_id||b.images!==10||b.time!==payload.scheduledText)throw new Error('scheduled-requery-mismatch:'+JSON.stringify({a,b}));
  const requery=Buffer.from(await p.screenshot()).toString('base64');
  emit({status:'scheduled_verified',provider_schedule_click_count:clicks,post_id:b.post_id,url:b.url,images:b.images,scheduled_at:payload.slot,provider_status_text:b.time,text_exact:true,page_reloaded:true,toast:toast||'composer_closed',precommit_png:precommit,requery_png:requery});
}catch(error){emit({status:clicks?'uncertain_after_click_do_not_retry':'blocked_no_click',provider_schedule_click_count:clicks,error:String(error?.message||error)});}
finally{try{if(p)await p.close();}catch(_){}}
'''
        return run_repl(code, cwd=upload_dir, timeout=600, account=account)


def schedule_verified(text: str, images: list[str], *, source_key: str, receipt: Path,
                      expected_channel: str, community_url: str, schedule_at: datetime | None,
                      account=None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", source_key):
        raise ValueError("Community source identity is invalid")
    if len(images) != 10:
        raise ValueError("Community requires exactly ten approved cards")
    source_urls = [f"https://youtu.be/{source_key}", f"https://www.youtube.com/watch?v={source_key}"]
    source_url = next((url for url in source_urls if url in text), "")
    if not source_url:
        raise ValueError("Community copy must contain one exact canonical source URL")
    channel_id = _channel_id(community_url)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    identity = dict(source_key=source_key, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    images_sha256=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images])
    PROVIDER_LOCK.touch(exist_ok=True)
    with PROVIDER_LOCK.open("a") as provider_lock, receipt.with_suffix(".lock").open("a") as item_lock:
        fcntl.flock(provider_lock, fcntl.LOCK_EX)
        fcntl.flock(item_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = json.loads(receipt.read_text()) if receipt.exists() else {}
        if prior and any(prior.get(k) != v for k, v in identity.items()):
            raise RuntimeError("Existing Community receipt binds different content; review required")
        if prior.get("status") == "published" and prior.get("verified") is True:
            return prior
        if prior.get("status") == "scheduled" and prior.get("verified") is True:
            if schedule_at is not None and prior.get("scheduled_at") != schedule_at.isoformat():
                raise RuntimeError("Community is already scheduled at a different verified slot")
            if prior.get("newly_scheduled") and not prior.get("crm_tracked"):
                prior["crm_tracked"] = track_external_event("youtube_community", source_key, stage="planned")
                _save_receipt(receipt, prior)
            return prior
        retryable_no_click = (prior.get("status") in {"reserved_schedule_verify_only", "blocked_no_click"}
                              and prior.get("provider_schedule_click_count") == 0
                              and (prior.get("provider_attempt") or {}).get("status") == "blocked_no_click")
        if prior and not retryable_no_click:
            raise RuntimeError("Community has an unresolved provider attempt; reconcile, do not retry")
        inventory = _schedule_inventory(channel_id=channel_id, expected_channel=expected_channel,
                                        community_url=community_url, account=account)
        related = [row for surface in ("public", "scheduled") for row in inventory[surface]["rows"]
                   if row.get("text", "").strip() == text.strip() or source_key in row.get("text", "")]
        if related:
            raise RuntimeError("Community public/scheduled duplicate found; reconcile, do not schedule")
        if schedule_at is None:
            schedule_at = _next_community_slot(inventory)
        policy = _validate_community_slot(schedule_at, inventory)
        reservation = dict(identity, status="reserved_schedule_verify_only", verified=False,
                           scheduled_at=schedule_at.isoformat(), attempted_at=datetime.now(timezone.utc).isoformat(),
                           provider_schedule_click_count=0, policy=policy)
        _save_receipt(receipt, reservation)
        provider = _schedule_provider(text, images, source_key=source_key, source_url=source_url, slot=schedule_at,
                                      channel_id=channel_id, expected_channel=expected_channel, account=account)
        reservation["provider_attempt"] = {key: value for key, value in provider.items()
                                           if key not in {"precommit_png", "requery_png"}}
        reservation["provider_schedule_click_count"] = provider.get("provider_schedule_click_count", 0)
        if provider.get("status") == "blocked_no_click" and provider.get("provider_schedule_click_count") == 0:
            reservation["status"] = "blocked_no_click"
        _save_receipt(receipt, reservation)
        if provider.get("status") != "scheduled_verified" or provider.get("provider_schedule_click_count") != 1:
            raise RuntimeError(f"Community schedule was not verified; do not retry: {provider}")
        for key, name in (("precommit_png", "schedule-precommit.png"), ("requery_png", "schedule-requery.png")):
            encoded = provider.get(key)
            if encoded:
                (receipt.parent / name).write_bytes(base64.b64decode(encoded))
        result = dict(identity, status="scheduled", verified=True, newly_scheduled=True,
                      url=provider["url"], post_id=provider["post_id"], images=provider["images"],
                      scheduled_at=schedule_at.isoformat(), provider_status_text=provider["provider_status_text"],
                      provider_schedule_click_count=1, page_reloaded=provider["page_reloaded"], policy=policy)
        _save_receipt(receipt, result)
        result["crm_tracked"] = track_external_event("youtube_community", source_key, stage="planned")
        _save_receipt(receipt, result)
        return result


def reconcile_scheduled(text: str, images: list[str], *, source_key: str, receipt: Path,
                        expected_channel: str, community_url: str, account=None) -> dict:
    if len(images) != 10 or not receipt.exists():
        raise RuntimeError("Scheduled reconciliation requires ten cards and an existing receipt")
    PROVIDER_LOCK.touch(exist_ok=True)
    with PROVIDER_LOCK.open("a") as provider_lock, receipt.with_suffix(".lock").open("a") as item_lock:
        fcntl.flock(provider_lock, fcntl.LOCK_EX)
        fcntl.flock(item_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = json.loads(receipt.read_text(encoding="utf-8"))
        identity = dict(source_key=source_key, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        images_sha256=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images])
        if any(prior.get(key) != value for key, value in identity.items()):
            raise RuntimeError("Existing Community receipt binds different content; review required")
        if prior.get("status") == "scheduled" and prior.get("verified") is True:
            if prior.get("newly_scheduled") and not prior.get("crm_tracked"):
                prior["crm_tracked"] = track_external_event("youtube_community", source_key, stage="planned")
                _save_receipt(receipt, prior)
            return prior
        if prior.get("provider_schedule_click_count") != 1:
            raise RuntimeError("No ambiguous schedule click exists to reconcile")
        scheduled_at = _parse_schedule_at(prior.get("scheduled_at", ""))
        channel_id = _channel_id(community_url)
        inventory = _schedule_inventory(channel_id=channel_id, expected_channel=expected_channel,
                                        community_url=community_url, account=account)
        public_matches = [row for row in inventory["public"]["rows"]
                          if row.get("text", "").strip() == text.strip() or source_key in row.get("text", "")]
        matches = [row for row in inventory["scheduled"]["rows"]
                   if row.get("text", "").strip() == text.strip() and source_key in row.get("text", "")]
        if public_matches or len(matches) != 1:
            raise RuntimeError(f"Scheduled reconciliation is ambiguous: public={len(public_matches)}, scheduled={len(matches)}")
        row = matches[0]
        observed = _inventory_times({"scheduled": {"rows": [row]}})[0]
        checks = {
            "exact_full_text": row.get("text", "").strip() == text.strip(),
            "source_url_exact": any(url in row.get("text", "") for url in
                                    (f"https://youtu.be/{source_key}", f"https://www.youtube.com/watch?v={source_key}")),
            "ten_images": row.get("image_count") == 10,
            "provider_id": bool(row.get("post_id")),
            "scheduled_at_exact": observed == scheduled_at,
            "full_inventory_exhausted": inventory["scheduled"].get("exhausted") is True,
        }
        if not all(checks.values()):
            raise RuntimeError(f"Scheduled reconciliation checks failed: {checks}")
        url = row.get("url") or f"https://www.youtube.com/post/{row['post_id']}"
        result = dict(identity, status="scheduled", verified=True, newly_scheduled=True,
                      url=url, post_id=row["post_id"], images=10, scheduled_at=scheduled_at.isoformat(),
                      provider_schedule_click_count=1, fresh_inventory_query=True, reconcile_checks=checks)
        _save_receipt(receipt, result)
        result["crm_tracked"] = track_external_event("youtube_community", source_key, stage="planned")
        _save_receipt(receipt, result)
        return result


def publish_verified(text: str, images: list[str], *, source_key: str, receipt: Path,
                     expected_channel: str, community_url: str = '', account=None,
                     verify_only: bool = False, record_observation: bool = False) -> dict:
    """A reserved attempt may only be reconciled, never blindly posted twice."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', source_key):
        raise ValueError('Community source identity is invalid')
    if len(images) != 10:
        raise ValueError('Community requires exactly ten approved cards')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    identity = dict(source_key=source_key, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    images_sha256=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images])
    with receipt.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = json.loads(receipt.read_text()) if receipt.exists() else {}
        if prior and any(prior.get(k) != v for k, v in identity.items()):
            raise RuntimeError('Existing Community receipt binds different content; review required')
        if prior.get('status') == 'published' and prior.get('verified') is True and not verify_only:
            if prior.get('newly_published') and not prior.get('crm_tracked') and not verify_only:
                prior['crm_tracked'] = track_publication('youtube_community', source_key)
                _save_receipt(receipt, prior)
            return prior
        newly_published = False
        scan = inspect_posts(text, len(images), source_key, expected_channel,
                             community_url=community_url, account=account)
        if scan.get('status') != 'verified':
            if verify_only:
                return {'status': 'unverified', 'source_key': source_key, 'scan': scan}
            # Older scheduled posts are not necessarily visible on the public
            # feed. Their evidence must never be mistaken for permission to post.
            legacy = any(re.search(r'youtube\.com/post/[A-Za-z0-9_-]+', p.read_text(encoding='utf-8'))
                         for p in receipt.parent.rglob('*.json') if p != receipt)
            if prior or legacy or scan.get('related_count') or not scan.get('exhausted'):
                raise RuntimeError('Community result ambiguous; verify only, do not repost')
            reservation = dict(identity, status='reserved_verify_only', verified=False,
                               attempted_at=datetime.now(timezone.utc).isoformat())
            _save_receipt(receipt, reservation)
            # The browser's old success toast is not accepted as a provider receipt.
            post_to_youtube_community(text, images, publish=True,
                                      community_url=community_url, expected_channel=expected_channel,
                                      account=account)
            newly_published = True
            scan = inspect_posts(text, len(images), source_key, expected_channel,
                                 community_url=community_url, account=account)
        provider = scan.get('provider') or {}
        if scan.get('status') != 'verified' or not provider.get('verified') or not re.fullmatch(
                r'https://www.youtube.com/post/[A-Za-z0-9_-]+', provider.get('url', '')):
            raise RuntimeError('Community direct post/body/cards verification failed; no repost')
        result = dict(identity, **provider, status='published', newly_published=newly_published)
        if verify_only:
            if record_observation:
                result['crm_status'] = 'historical_observation_no_new_send'
                _save_receipt(receipt, result)
            return result
        # Save provider success before tracking so tracker failure never reposts.
        _save_receipt(receipt, result)
        result['crm_tracked'] = track_publication('youtube_community', source_key) if newly_published else False
        if not newly_published:
            result['crm_status'] = 'historical_observation_no_new_send'
        _save_receipt(receipt, result)
        return result


def load_config() -> dict[str, str]:
    cfg = configparser.RawConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return {
        "community_url": cfg.get("YOUTUBE", "community_url", fallback="").strip(),
        "expected_channel": cfg.get("YOUTUBE", "expected_channel", fallback="").strip(),
        "aside_account": cfg.get("BROWSER", "aside_account", fallback="").strip(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--images", nargs="*", default=[])
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--schedule-at", default="", help="Reserve at an ISO-8601 Asia/Seoul time; never immediate-publish")
    parser.add_argument("--schedule-next", action="store_true", help="Reserve at the next verified 11:00/20:00 KST slot")
    parser.add_argument("--community-url", default="")
    parser.add_argument("--expected-channel", default="")
    parser.add_argument("--aside-account", default="")
    parser.add_argument("--preview-path", default="")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-key", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--reconcile", action="store_true", help="Read and save existing provider proof; never post")
    args = parser.parse_args(argv)

    text_path = Path(args.text_file).expanduser().resolve()
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        parser.error("--text-file 내용이 비어 있습니다.")
    if len(args.images) > 10:
        parser.error("YouTube 커뮤니티 이미지는 최대 10장입니다.")

    cfg = load_config()
    expected = args.expected_channel or cfg["expected_channel"]
    if sum(bool(value) for value in (args.publish, args.schedule_at, args.schedule_next)) > 1:
        parser.error('--publish, --schedule-at, and --schedule-next are mutually exclusive')
    if args.publish and (args.verify_only or args.reconcile):
        parser.error('--publish cannot be combined with read-only verification')
    if (args.schedule_at or args.schedule_next) and (args.verify_only or args.reconcile):
        parser.error('scheduling cannot be combined with read-only verification')
    if (args.publish or args.schedule_at or args.schedule_next or args.verify_only or args.reconcile) and not expected:
        parser.error("--publish에는 오발행 방지를 위해 --expected-channel이 필요합니다.")

    if args.publish or args.schedule_at or args.schedule_next or args.verify_only or args.reconcile:
        key = args.source_key or next(iter(re.findall(r'(?:youtu\.be/|[?&]v=)([A-Za-z0-9_-]{11})', text)), '')
        receipt = args.receipt or text_path.parent / 'provider/publish_receipt.json'
        community_url = args.community_url or cfg['community_url']
        account = args.aside_account or cfg['aside_account'] or None
        if args.schedule_at or args.schedule_next:
            result = schedule_verified(text, args.images, source_key=key, receipt=receipt,
                                       expected_channel=expected, community_url=community_url,
                                       schedule_at=_parse_schedule_at(args.schedule_at) if args.schedule_at else None,
                                       account=account)
        elif args.reconcile and receipt.exists() and json.loads(receipt.read_text()).get('provider_schedule_click_count') == 1:
            result = reconcile_scheduled(text, args.images, source_key=key, receipt=receipt,
                                         expected_channel=expected, community_url=community_url,
                                         account=account)
        else:
            result = publish_verified(text, args.images, source_key=key, receipt=receipt,
                                      expected_channel=expected, community_url=community_url,
                                      account=account, verify_only=args.verify_only or args.reconcile,
                                      record_observation=args.reconcile)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('status') in {'published', 'scheduled'} else 1

    result = post_to_youtube_community(
        text,
        args.images,
        publish=args.publish,
        community_url=args.community_url or cfg["community_url"],
        expected_channel=expected,
        preview_path=args.preview_path or None,
        account=args.aside_account or cfg["aside_account"] or None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
