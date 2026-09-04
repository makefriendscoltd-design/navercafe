#!/usr/bin/env python3
"""Concrete Aside-u0 and CRM ports for :mod:`youtube_shorts_publisher`.

The policy/state machine lives in ``youtube_shorts_publisher.py``.  This file
only translates that final port contract to the approved provider surfaces:
Aside CLI's headless ``u0`` session and ``aimax-crm-track``.  Importing this
module and running ``--dry-validate`` never imports ``aside_browser``, opens
Studio, reads the CRM ledger, or invokes the tracker.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import youtube_shorts_publisher as publisher


KST = ZoneInfo("Asia/Seoul")
ACCOUNT = "u0"
HEADLESS = True
CHANNEL_NAME = "나민수 AI"
CHANNEL_ID = "UCWyi-m_CdIbRpcwZN6MgBfg"
STUDIO_SHORTS_URL = f"https://studio.youtube.com/channel/{CHANNEL_ID}/videos/short"
PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")
TRACKER = Path("/Users/apple/orca/projects/aimax-crm-observability/bin/aimax-crm-track")
CRM_DB = Path("/Users/apple/Library/Application Support/AIMAX CRM Observability/events.sqlite3")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LiveDependencyError(publisher.PublisherSafetyError):
    """A live-only executable, login context, or response is unavailable."""


class AdapterEvidenceError(publisher.InventoryError):
    """Aside returned evidence that cannot satisfy the publisher contract."""


AsideRunner = Callable[..., Mapping[str, Any]]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _kst_now() -> datetime:
    return datetime.now(KST)


def _require_exact_channel(manifest: publisher.PublishManifest) -> None:
    if manifest.expected_channel != CHANNEL_NAME:
        raise LiveDependencyError(
            f"this adapter is sealed to channel {CHANNEL_NAME!r}; got {manifest.expected_channel!r}"
        )


def _default_aside_runner() -> AsideRunner:
    """Resolve the approved runner lazily so dry validation has zero access."""

    try:
        from aside_browser import JS_COMMON, _payload_expression, resolve_aside_cli, run_repl
    except Exception as exc:  # pragma: no cover - exercised through injected failure
        raise LiveDependencyError("Aside CLI integration module is unavailable") from exc
    binary = resolve_aside_cli()
    if binary is None or not Path(binary).is_file():
        raise LiveDependencyError(
            "Aside CLI is unavailable; install it from Aside > Settings > Developers"
        )

    def run(body: str, payload: Mapping[str, Any], *, cwd: Path, timeout: int) -> Mapping[str, Any]:
        code = JS_COMMON + "\nconst payload=" + _payload_expression(dict(payload)) + ";\n" + body
        return run_repl(code, cwd=cwd, timeout=timeout, account=ACCOUNT)

    return run


def _iso_from_provider_parts(date_text: str, time_text: str) -> str:
    """Parse Studio's Korean/ISO schedule controls into an explicit KST ISO value."""

    date_match = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", date_text)
    time_match = re.search(r"(?:(오전|오후|AM|PM)\s*)?(\d{1,2}):(\d{2})", time_text, re.I)
    if not date_match or not time_match:
        raise AdapterEvidenceError(
            f"Studio schedule controls are not an exact date/time: {date_text!r}, {time_text!r}"
        )
    year, month, day = map(int, date_match.groups())
    marker, hour_text, minute_text = time_match.groups()
    hour, minute = int(hour_text), int(minute_text)
    if marker and marker.upper() in {"오후", "PM"} and hour < 12:
        hour += 12
    if marker and marker.upper() in {"오전", "AM"} and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, tzinfo=KST).isoformat()


def _exact_kst_iso(value: object, *, date_text: str = "", time_text: str = "") -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AdapterEvidenceError(f"provider timestamp is not ISO: {value!r}") from exc
        if parsed.tzinfo is None:
            raise AdapterEvidenceError("provider timestamp is missing a timezone")
        return parsed.astimezone(KST).isoformat()
    return _iso_from_provider_parts(date_text, time_text)


def _exact_kst_date(value: object) -> str:
    """Preserve Studio's date-only public-row evidence without inventing a time."""

    text = str(value or "").strip()
    match = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text)
    if not match:
        raise AdapterEvidenceError(f"Studio public date is not exact: {text!r}")
    year, month, day = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise AdapterEvidenceError(f"Studio public date is invalid: {text!r}") from exc


def _timezone_contract_present(value: object) -> bool:
    """Accept the current generic button label or an explicit GMT+09 label."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return bool(
        text in {"시간대", "Timezone"}
        or re.search(r"GMT\s*\+\s*0?9(?::?00)?|서울|Asia/Seoul", text, re.I)
    )


INVENTORY_JS = r"""
let list=null,edit=null;
const pages=[],all=[],seen=new Set(),states=['public','scheduled','private','draft'];
const counts={public:0,scheduled:0,private:0,draft:0};
const visible=el=>{if(!el)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
const context=async(p,mode)=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(({channel,mode})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),avatar=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),title=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),description=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis),rows=[...document.querySelectorAll('ytcp-video-row')],next=[...document.querySelectorAll('#navigate-after,ytcp-icon-button#navigate-after')];return{host,path,signinRedirect,channelExact:lines.includes(channel),avatarCount:avatar.length,titleCount:title.length,descriptionCount:description.length,rowCount:rows.length,nextCount:next.length,ready:host==='studio.youtube.com'&&!signinRedirect&&lines.includes(channel)&&avatar.length===1&&(mode==='edit'?title.length===1&&description.length===1:rows.length>0&&next.length===1)};},{channel:payload.channel,mode});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error(`login-required:${state.host}${state.path}`);if(!state.ready)throw new Error(`studio-context-unverified:${mode}:${JSON.stringify(state)}`);return state;};
const one=async(root,selector,label,{mustBeVisible=true}={})=>{const loc=root.locator(selector);const count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const statusOf=text=>{const lines=text.split('\n').map(x=>x.trim()).filter(Boolean);if(lines.some(x=>/^(예약됨|Scheduled)$/i.test(x)))return 'scheduled';if(lines.some(x=>/^(비공개|Private)$/i.test(x)))return 'private';if(lines.some(x=>/^(공개|Public)$/i.test(x)))return 'public';if(lines.some(x=>/^(초안|Draft)$/i.test(x)))return 'draft';return '';};
const rowOf=async(row,page,rowIndex)=>{const text=((await row.innerText())||'').trim(),titleNodes=row.locator('#video-title'),titleCount=await titleNodes.count();if(titleCount!==1)throw new Error(`row-title-cardinality:${page}:${rowIndex}:${titleCount}`);if(!await titleNodes.isVisible())throw new Error(`row-title-not-visible:${page}:${rowIndex}`);const titleNode=titleNodes,title=(((await titleNode.getAttribute('title'))||(await titleNode.innerText())||'').trim()),hrefs=await row.locator('a[href]').evaluateAll(nodes=>nodes.map(a=>a.href)),images=row.locator('img'),imageCount=await images.count(),src=imageCount?((await images.nth(0).getAttribute('src'))||''):'';const id=([...hrefs,src].join('\n').match(/(?:\/video\/|[?&]v=|\/shorts\/|\/vi\/)([A-Za-z0-9_-]{11})/)||[])[1]||'',status=statusOf(text),dateNodes=row.locator('#date,time[datetime]'),dateCount=await dateNodes.count();if(dateCount!==1)throw new Error(`row-date-cardinality:${page}:${rowIndex}:${dateCount}`);if(!await dateNodes.isVisible())throw new Error(`row-date-not-visible:${page}:${rowIndex}`);const publishedRaw=((await dateNodes.getAttribute('datetime'))||(await dateNodes.getAttribute('title'))||(await dateNodes.innerText())||'').trim();if(!id)throw new Error(`provider-id-missing:${page}:${rowIndex}`);if(!status)throw new Error(`unknown-state:${id}`);return {identity:id,provider_id:id,status,title,listText:text,urls:hrefs,page,publishedRaw};};
try{
 list=await openTab(`${payload.list_url}?strict_inventory=${Date.now()}`);await context(list,'list');
 for(let pageIndex=1;pageIndex<=80;pageIndex++){
  await list.evaluate(()=>window.scrollTo(0,0));let stable=0,last=-1;for(let i=0;i<80&&stable<4;i++){const rowSet=list.locator('ytcp-video-row'),n=await rowSet.count();stable=n===last?stable+1:0;last=n;if(n)await rowSet.nth(n-1).scrollIntoViewIfNeeded();await list.evaluate(()=>window.scrollBy(0,Math.max(700,window.innerHeight*.85)));await sleep(350);}
  const rowSet=list.locator('ytcp-video-row'),n=await rowSet.count();if(!n)throw new Error(`empty-page:${pageIndex}`);const first=await rowOf(rowSet.nth(0),pageIndex,0),pageCounts={public:0,scheduled:0,private:0,draft:0};
  for(let i=0;i<n;i++){const item=i===0?first:await rowOf(rowSet.nth(i),pageIndex,i);if(seen.has(item.identity))throw new Error(`duplicate-row:${item.identity}`);seen.add(item.identity);all.push(item);counts[item.status]++;pageCounts[item.status]++;}
  const next=await one(list,'#navigate-after,ytcp-icon-button#navigate-after','pagination-next',{mustBeVisible:true});const disabled=(await next.getAttribute('aria-disabled'))==='true'||(await next.getAttribute('disabled'))!==null||!await next.isEnabled();pages.push({page:pageIndex,row_count:n,state_counts:pageCounts,next_disabled:disabled});if(disabled)break;
  await next.click();let changed=false,end=Date.now()+15000;while(Date.now()<end&&!changed){await sleep(300);const after=list.locator('ytcp-video-row');if(await after.count()){const candidate=await rowOf(after.nth(0),pageIndex+1,0);changed=candidate.identity!==first.identity;}}if(!changed)throw new Error(`pagination-did-not-advance:${pageIndex}`);
 }
 if(!pages.length||pages.at(-1).next_disabled!==true)throw new Error('pagination-limit-before-next-disabled');await list.close();list=null;
 const uploadFlowIds=new Set();for(const tab of await listBrowserTabs()){if(!String(tab.url||'').startsWith('https://studio.youtube.com/'))continue;let flow=null;try{flow=await attachBrowserTab(tab.targetId);const dialogs=flow.locator('ytcp-uploads-dialog');if(await dialogs.count()!==1||!await dialogs.isVisible())continue;const titles=dialogs.locator('#title-textarea #textbox');if(await titles.count()!==1||!await titles.isVisible())continue;const flowTitle=((await titles.innerText())||'').trim();if(![payload.sentinel,payload.title].includes(flowTitle))continue;const body=(await dialogs.innerText())||'',values=await dialogs.locator('a[href],input').evaluateAll(nodes=>nodes.map(x=>x.href||x.value||'')),id=([...values,body].join('\n').match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/video\/)([A-Za-z0-9_-]{11})/)||[])[1]||'';const badges=dialogs.locator('#step-badge-3');if(id&&await badges.count()===1&&await badges.isVisible())uploadFlowIds.add(id);}catch(_){}}
 const rows=[];
 for(const seed of all){
  edit=await openTab(`https://studio.youtube.com/video/${seed.provider_id}/edit?strict_metadata=${Date.now()}`);await context(edit,'edit');const route=(edit.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'';if(route!==seed.provider_id)throw new Error(`direct-route-mismatch:${seed.provider_id}`);
  const titleBox=await one(edit,'#title-textarea #textbox','metadata-title'),descriptionBox=await one(edit,'#description-textarea #textbox','metadata-description'),title=((await titleBox.innerText())||'').trim(),description=((await descriptionBox.innerText())||'').trim();
  const noKidsSet=edit.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'),visibilitySet=edit.locator('ytcp-video-metadata-visibility #container'),visibilityCount=await visibilitySet.count();if(visibilityCount>1)throw new Error(`visibility-control-cardinality:${seed.provider_id}:${visibilityCount}`);let scheduled_at='',scheduleDate='',scheduleTime='',timezoneEvidence='';if(seed.status==='scheduled'){if(visibilityCount!==1||!await visibilitySet.isVisible())throw new Error(`schedule-visibility-missing:${seed.provider_id}`);await visibilitySet.click();await sleep(450);const date=await one(edit,'#datepicker-trigger','schedule-date'),timeInput=await one(edit,'#time-of-day-container input','schedule-time'),tz=await one(edit,'#timezone-select-button,#timezone-select-trigger','schedule-timezone');scheduleDate=((await date.innerText())||'').replace(/\s+/g,' ').trim();scheduleTime=await timeInput.inputValue();timezoneEvidence=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(!timezoneEvidence)throw new Error(`schedule-timezone-empty:${seed.provider_id}`);}
  const noKidsCount=await noKidsSet.count();if(noKidsCount>1)throw new Error(`no-kids-cardinality:${seed.provider_id}:${noKidsCount}`);rows.push({...seed,title,description,urls:[...new Set([...seed.urls,...((description.match(/https?:\/\/[^\s]+/g))||[])])],direct_metadata_inspected:true,visibility_control_present:visibilityCount===1||uploadFlowIds.has(seed.provider_id),upload_flow_present:uploadFlowIds.has(seed.provider_id),scheduled_at,schedule_date:scheduleDate,schedule_time:scheduleTime,timezone_evidence:timezoneEvidence,no_kids:noKidsCount===1&&(await noKidsSet.getAttribute('aria-checked'))==='true',published_at:seed.status==='public'?seed.publishedRaw:''});await edit.close();edit=null;
 }
 emit({status:'pass',account:'u0',headless:true,channel:payload.channel,pagination_complete:true,terminal_reason:'next_disabled',pages_scanned:pages.length,pages,scanned_states:states,status_counts:counts,rows});
}catch(error){emit({status:'blocked',account:'u0',headless:true,channel:payload.channel,pagination_complete:false,terminal_reason:'error',pages_scanned:pages.length,pages,scanned_states:states,status_counts:counts,rows:[],error:String(error?.message||error)});}finally{try{if(edit)await edit.close();}catch(_){}try{if(list)await list.close();}catch(_){}}
"""


ATTACH_JS = r"""
let p=null,attachActions=0,success=false;const compact=s=>(s||'').replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').replace(/\u00A0/g,' ').replace(/\r\n?/g,'\n').replace(/\s+/g,'');
const waitContext=async()=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(channel=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),avatarCount:avatars.length,ready:host==='studio.youtube.com'&&!signinRedirect&&lines.includes(channel)&&avatars.length===1};},payload.channel);if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${JSON.stringify(state)}`);};
const exact=async(root,selector,label,{mustBeVisible=true}={})=>{const loc=root.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
try{p=await openTab(`https://studio.youtube.com/?shorts_upload_session=${payload.session_marker}`);await waitContext();const clickUpload=()=>p.evaluate(()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const found=[...document.querySelectorAll('button,[role="button"],ytcp-button,ytcp-icon-button,tp-yt-paper-item')].filter(e=>vis(e)&&(e.id==='upload-icon'||/동영상 업로드|Upload videos/i.test((e.getAttribute('aria-label')||'')+' '+(e.innerText||''))));if(found.length!==1)return false;found[0].click();return true;});let opened=await clickUpload();if(!opened){const createSet=p.getByText(/^(만들기|Create)$/),createCount=await createSet.count();if(createCount!==1||!await createSet.isVisible())throw new Error(`create-control-cardinality:${createCount}`);await createSet.click();await sleep(400);opened=await clickUpload();}if(!opened)throw new Error('upload-control-missing');await sleep(700);const input=await exact(p,'input[type="file"][name="Filedata"],input[type="file"]','file-input',{mustBeVisible:false});if(payload.file.name!=='final.mp4'||!payload.file.base64)throw new Error('candidate-payload-invalid');attachActions++;await input.setInputFiles([{name:payload.file.name,mimeType:'video/mp4',buffer:Buffer.from(payload.file.base64,'base64')}]);const dialogEnd=Date.now()+120000;let dialogSet=p.locator('ytcp-uploads-dialog');while(Date.now()<dialogEnd&&(await dialogSet.count()!==1||!await dialogSet.isVisible()))await sleep(500);const dialog=await exact(p,'ytcp-uploads-dialog','upload-dialog'),title=await exact(dialog,'#title-textarea #textbox','upload-title');await title.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await title.pressSequentially(payload.sentinel,{delay:1});if(compact(await title.innerText())!==compact(payload.sentinel))throw new Error('sentinel-fill-mismatch');let body='',providerId='',observed=false;const observedEnd=Date.now()+180000;while(Date.now()<observedEnd&&!observed){body=await dialog.innerText();const values=await dialog.locator('a[href],input').evaluateAll(nodes=>nodes.map(x=>x.href||x.value||'')),badges=dialog.locator('#step-badge-3');providerId=([...values,body].join('\n').match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/video\/)([A-Za-z0-9_-]{11})/)||[])[1]||'';observed=body.includes('final.mp4')&&!!providerId&&await badges.count()===1;if(!observed)await sleep(700);}if(!observed||attachActions!==1)throw new Error('provider-attachment-or-visibility-flow-receipt-missing');success=true;emit({status:'attached',account:'u0',headless:true,channel:payload.channel,provider_observed_attachment_click_count:attachActions,attachment_name:'final.mp4',attachment_sha256:payload.sha256,draft_sentinel:payload.sentinel,provider_id:providerId,displayed_filename:true,upload_flow_preserved:true,session_marker:payload.session_marker});}catch(error){emit({status:'blocked',account:'u0',headless:true,channel:payload.channel,provider_observed_attachment_click_count:attachActions,error:String(error?.message||error)});}finally{try{if(p&&attachActions===0)await p.close();}catch(_){}}
"""


SCHEDULE_JS = r"""
let p=null,root=null,scheduleClicks=0,flow='';const compact=s=>(s||'').replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').replace(/\u00A0/g,' ').replace(/\r\n?/g,'\n').replace(/\s+/g,'');
const exact=async(base,selector,label,{mustBeVisible=true}={})=>{const loc=base.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const context=async(page,mode)=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await page.evaluate(({channel,mode})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),titles=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),descriptions=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),avatarCount:avatars.length,titleCount:titles.length,descriptionCount:descriptions.length,ready:host==='studio.youtube.com'&&!signinRedirect&&lines.includes(channel)&&avatars.length===1&&(mode==='edit'?titles.length===1&&descriptions.length===1:true)};},{channel:payload.channel,mode});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${mode}:${JSON.stringify(state)}`);};
const fill=async(loc,value)=>{await loc.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await loc.pressSequentially(value,{delay:1});return compact(await loc.innerText())===compact(value);};
try{if(payload.identity!==payload.provider_id)throw new Error('exact-draft-identity-mismatch');const matches=[];for(const tab of await listBrowserTabs()){if(!String(tab.url||'').startsWith('https://studio.youtube.com/'))continue;try{const candidate=await attachBrowserTab(tab.targetId),dialogs=candidate.locator('ytcp-uploads-dialog');if(await dialogs.count()!==1||!await dialogs.isVisible())continue;const titles=dialogs.locator('#title-textarea #textbox');if(await titles.count()!==1||!await titles.isVisible())continue;const before=((await titles.innerText())||'').trim();if(![payload.sentinel,payload.title].includes(before))continue;const body=(await dialogs.innerText())||'',values=await dialogs.locator('a[href],input').evaluateAll(nodes=>nodes.map(x=>x.href||x.value||'')),id=([...values,body].join('\n').match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/video\/)([A-Za-z0-9_-]{11})/)||[])[1]||'';if(id===payload.provider_id)matches.push({page:candidate,dialog:dialogs});}catch(_){}}if(matches.length>1)throw new Error(`upload-flow-cardinality:${matches.length}`);if(matches.length===1){p=matches[0].page;root=matches[0].dialog;flow='upload';await context(p,'upload');const badge=await exact(root,'#step-badge-3','visibility-step');const readyEnd=Date.now()+180000;while(Date.now()<readyEnd&&(!await badge.isEnabled()||(await badge.getAttribute('aria-disabled'))==='true'))await sleep(700);if(!await badge.isEnabled())throw new Error('visibility-step-not-ready');await badge.click();await sleep(900);}else{p=await openTab(`https://studio.youtube.com/video/${payload.provider_id}/edit?exact_draft_recovery=${Date.now()}`);await context(p,'edit');const route=(p.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'';if(route!==payload.provider_id)throw new Error('exact-draft-route-mismatch');root=p;flow='direct';const visibility=await exact(root,'ytcp-video-metadata-visibility #container','direct-visibility');await visibility.click();await sleep(600);}
 const title=await exact(root,'#title-textarea #textbox','metadata-title'),description=await exact(root,'#description-textarea #textbox','metadata-description'),beforeTitle=((await title.innerText())||'').trim();if(![payload.sentinel,payload.title].includes(beforeTitle))throw new Error('draft-sentinel-mismatch');if(!await fill(title,payload.title)||!await fill(description,payload.description))throw new Error('metadata-fill-mismatch');let noKidsSet=root.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');if(await noKidsSet.count()===0){const expandSet=root.locator('#second-container-expand-button');if(await expandSet.count()===1&&await expandSet.isVisible()){await expandSet.click();await sleep(400);}noKidsSet=root.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');}if(await noKidsSet.count()!==1||!await noKidsSet.isVisible())throw new Error(`no-kids-control-cardinality:${await noKidsSet.count()}`);if((await noKidsSet.getAttribute('aria-checked'))!=='true'){await noKidsSet.click();await sleep(350);}if((await noKidsSet.getAttribute('aria-checked'))!=='true')throw new Error('no-kids-not-selected');const scheduleSelector=flow==='upload'?'#second-container':'tp-yt-paper-radio-button[name="SCHEDULE"],[role="radio"][name="SCHEDULE"],#publish-from-private-non-sponsor-selector',schedule=await exact(root,scheduleSelector,'schedule-selector');if(flow==='upload'||(await schedule.getAttribute('aria-checked'))!=='true'){await schedule.click();await sleep(650);}let tz=await exact(root,'#timezone-select-button,#timezone-select-trigger','timezone-control');await tz.click();await sleep(350);const zones=p.getByText(payload.timezone_label,{exact:true}),zoneCount=await zones.count();if(zoneCount!==1||!await zones.isVisible())throw new Error(`timezone-option-cardinality:${zoneCount}`);await zones.click();await sleep(650);let dateControl=await exact(root,'#datepicker-trigger','schedule-date'),timeControl=await exact(root,'#time-of-day-container input','schedule-time');await dateControl.click();await sleep(250);const dateInput=await exact(root,'ytcp-date-picker input','date-input');await dateInput.fill(payload.provider_date);await dateInput.press('Enter');await sleep(450);timeControl=await exact(root,'#time-of-day-container input','schedule-time-after-date');await timeControl.fill(payload.provider_time);await timeControl.press('Tab');let selectedDate='',selectedTime='',timezoneLabel='';const stableEnd=Date.now()+12000;while(Date.now()<stableEnd){dateControl=await exact(root,'#datepicker-trigger','schedule-date-after-fill');timeControl=await exact(root,'#time-of-day-container input','schedule-time-after-fill');tz=await exact(root,'#timezone-select-button,#timezone-select-trigger','timezone-after-fill');selectedDate=((await dateControl.innerText())||'').replace(/\s+/g,' ').trim();selectedTime=await timeControl.inputValue();timezoneLabel=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(selectedDate===payload.provider_date&&selectedTime===payload.provider_time&&timezoneLabel)break;await sleep(300);}const landedTitle=((await title.innerText())||'').trim(),landedDescription=((await description.innerText())||'').trim();if(landedTitle!==payload.title||compact(landedDescription)!==compact(payload.description)||!payload.urls.every(url=>landedDescription.includes(url))||selectedDate!==payload.provider_date||selectedTime!==payload.provider_time||!timezoneLabel)throw new Error('schedule-precommit-evidence-mismatch');const done=await exact(root,'#done-button','done-button'),innerButtons=done.locator('button'),innerCount=await innerButtons.count(),doneText=((await done.innerText())||'').trim(),doneAria=innerCount===1?((await innerButtons.getAttribute('aria-label'))||''):((await done.getAttribute('aria-label'))||'');if(innerCount>1||!await done.isEnabled()||(await done.getAttribute('aria-disabled'))==='true'||!/^(예약|Schedule)$/.test(doneText)||!/^(예약|Schedule)$/.test(doneAria))throw new Error('single-schedule-control-not-ready');scheduleClicks=1;await done.click();let confirmed=false,body='';const end=Date.now()+90000;while(Date.now()<end&&!confirmed){await sleep(700);body=await p.locator('body').innerText();confirmed=/동영상 예약됨|Video scheduled/i.test(body)||await root.locator('#done-button').count()===0;}if(!confirmed)throw new Error('schedule-confirmation-missing');emit({status:'committed',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,provider_observed_schedule_click_count:scheduleClicks,title:landedTitle,description:landedDescription,no_kids:true,scheduled_at:payload.slot,timezone:'Asia/Seoul',flow});}catch(error){emit({status:scheduleClicks===0?'blocked':'uncertain_after_click_do_not_retry',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,provider_observed_schedule_click_count:scheduleClicks,flow,error:String(error?.message||error)});}finally{try{if(p&&!(flow==='upload'&&scheduleClicks===0))await p.close();}catch(_){}}
"""


DIRECT_JS = r"""
let p=null;const exact=async(base,selector,label)=>{const loc=base.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const context=async()=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(channel=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),titles=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),descriptions=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),avatarCount:avatars.length,titleCount:titles.length,descriptionCount:descriptions.length,ready:host==='studio.youtube.com'&&!signinRedirect&&lines.includes(channel)&&avatars.length===1&&titles.length===1&&descriptions.length===1};},payload.channel);if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${JSON.stringify(state)}`);};
try{p=await openTab(`https://studio.youtube.com/video/${payload.provider_id}/edit?strict_direct_requery=${Date.now()}`);await context();const route=(p.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'',body=await p.locator('body').innerText();if(route!==payload.provider_id)throw new Error('direct-route-mismatch');const title=await exact(p,'#title-textarea #textbox','direct-title'),description=await exact(p,'#description-textarea #textbox','direct-description'),noKids=await exact(p,'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]','direct-no-kids'),visibility=await exact(p,'ytcp-video-metadata-visibility #container','direct-visibility');await visibility.click();await sleep(500);const dateControl=await exact(p,'#datepicker-trigger','direct-date'),timeControl=await exact(p,'#time-of-day-container input','direct-time'),tz=await exact(p,'#timezone-select-button,#timezone-select-trigger','direct-timezone'),titleText=((await title.innerText())||'').trim(),descriptionText=((await description.innerText())||'').trim(),dateText=((await dateControl.innerText())||'').replace(/\s+/g,' ').trim(),timeText=await timeControl.inputValue(),timezone=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(!timezone)throw new Error('direct-timezone-empty');emit({status:'pass',account:'u0',headless:true,channel:payload.channel,provider_id:route,shorts_url:`https://www.youtube.com/shorts/${route}`,exact_row_count:1,title:titleText,description:descriptionText,original_urls:payload.urls.filter(url=>descriptionText.includes(url)),no_kids:(await noKids.getAttribute('aria-checked'))==='true',provider_status:/예약됨|Scheduled/i.test(body)?'scheduled':'unknown',schedule_date:dateText,schedule_time:timeText,timezone_evidence:timezone});}catch(error){emit({status:'blocked',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,error:String(error?.message||error)});}finally{try{if(p)await p.close();}catch(_){}}
"""


class AsideHeadlessU0Provider:
    """Final ``ProviderPort`` implementation, sealed to Aside headless u0."""

    account = ACCOUNT
    headless = HEADLESS

    def __init__(
        self,
        *,
        runner: AsideRunner | None = None,
        clock: Callable[[], datetime] = _kst_now,
    ):
        self._runner = runner
        self._clock = clock

    def _run(
        self, body: str, payload: Mapping[str, Any], *, cwd: Path, timeout: int
    ) -> Mapping[str, Any]:
        runner = self._runner or _default_aside_runner()
        try:
            result = runner(body, payload, cwd=cwd, timeout=timeout)
        except LiveDependencyError:
            raise
        except Exception as exc:
            raise LiveDependencyError("Aside CLI headless u0 call failed") from exc
        if not isinstance(result, Mapping):
            raise AdapterEvidenceError("Aside returned a non-object result")
        return result

    def scan_inventory(
        self, manifest: publisher.PublishManifest, *, phase: str
    ) -> Mapping[str, Any]:
        _require_exact_channel(manifest)
        raw = self._run(
            INVENTORY_JS,
            {
                "list_url": STUDIO_SHORTS_URL,
                "channel": manifest.expected_channel,
                "sentinel": manifest.draft_sentinel,
                "title": manifest.title,
            },
            cwd=manifest.video.parent,
            timeout=1200,
        )
        if (
            raw.get("status") != "pass"
            or raw.get("pagination_complete") is not True
            or raw.get("terminal_reason") != "next_disabled"
        ):
            raise AdapterEvidenceError(
                f"exhaustive Studio inventory failed: {raw.get('error') or raw.get('status')}"
            )
        if raw.get("account") != ACCOUNT or raw.get("headless") is not True:
            raise AdapterEvidenceError("inventory is not bound to Aside headless u0")
        if raw.get("channel") != manifest.expected_channel:
            raise AdapterEvidenceError("inventory channel evidence differs")
        pages = raw.get("pages")
        rows = raw.get("rows")
        if not isinstance(pages, list) or not pages or not isinstance(rows, list):
            raise AdapterEvidenceError("inventory pages or rows are missing")
        if int(raw.get("pages_scanned") or 0) != len(pages):
            raise AdapterEvidenceError("inventory page cardinality differs")
        if pages[-1].get("next_disabled") is not True:
            raise AdapterEvidenceError("inventory final page has no disabled-next evidence")
        page_numbers = [int(item.get("page") or 0) for item in pages]
        if page_numbers != list(range(1, len(pages) + 1)):
            raise AdapterEvidenceError("inventory page sequence is not exhaustive")

        normalized: list[dict[str, Any]] = []
        for value in rows:
            if not isinstance(value, Mapping):
                raise AdapterEvidenceError("inventory row is not an object")
            status = str(value.get("status") or "")
            if status not in publisher.STATES:
                raise AdapterEvidenceError("inventory contains an unknown state")
            provider_id = str(value.get("provider_id") or "")
            identity = str(value.get("identity") or "")
            if not VIDEO_ID_RE.fullmatch(provider_id) or identity != provider_id:
                raise AdapterEvidenceError("inventory row lacks an exact provider identity")
            if value.get("direct_metadata_inspected") is not True:
                raise AdapterEvidenceError("inventory row lacks direct metadata inspection")
            item: dict[str, Any] = {
                "identity": identity,
                "provider_id": provider_id,
                "status": status,
                "title": str(value.get("title") or ""),
                "description": str(value.get("description") or ""),
                "urls": list(value.get("urls") or []),
                "page": int(value.get("page") or 0),
                "direct_metadata_inspected": True,
                "visibility_control_present": value.get("visibility_control_present"),
            }
            if status == "scheduled":
                timezone_evidence = str(value.get("timezone_evidence") or "")
                if not _timezone_contract_present(timezone_evidence):
                    raise AdapterEvidenceError(
                        "scheduled row lacks the current Studio timezone control contract"
                    )
                item["scheduled_at"] = _exact_kst_iso(
                    value.get("scheduled_at"),
                    date_text=str(value.get("schedule_date") or ""),
                    time_text=str(value.get("schedule_time") or ""),
                )
            if status == "public":
                published = value.get("published_at")
                try:
                    item["published_at"] = _exact_kst_iso(published)
                except AdapterEvidenceError:
                    item["published_date"] = _exact_kst_date(published)
            normalized.append(item)
        identities = [item["identity"] for item in normalized]
        if len(set(identities)) != len(identities):
            raise AdapterEvidenceError("inventory identities are duplicated")
        calculated = {
            state: sum(item["status"] == state for item in normalized)
            for state in publisher.STATES
        }
        supplied = raw.get("status_counts") or {}
        if any(int(supplied.get(state, -1)) != calculated[state] for state in publisher.STATES):
            raise AdapterEvidenceError("inventory state evidence does not account for every row")
        if set(raw.get("scanned_states") or []) != publisher.STATES:
            raise AdapterEvidenceError("inventory did not scan all four provider states")
        captured = self._clock()
        if captured.tzinfo is None:
            raise AdapterEvidenceError("adapter clock must be timezone-aware")
        return {
            "status": "pass",
            "scan_id": str(uuid.uuid4()),
            "phase": phase,
            "captured_at": captured.astimezone(KST).isoformat(),
            "account": ACCOUNT,
            "headless": True,
            "channel": manifest.expected_channel,
            "pagination_complete": True,
            "terminal_reason": "next_disabled",
            "pages_scanned": len(pages),
            "pages": pages,
            "scanned_states": sorted(publisher.STATES),
            "status_counts": calculated,
            "rows": normalized,
        }

    def attach_once(
        self, manifest: publisher.PublishManifest, *, draft_sentinel: str
    ) -> Mapping[str, Any]:
        _require_exact_channel(manifest)
        if manifest.video.name != "final.mp4":
            raise publisher.ManifestError("adapter accepts only exact final.mp4")
        encoded = base64.b64encode(manifest.video.read_bytes()).decode("ascii")
        session_marker = uuid.uuid4().hex
        raw = self._run(
            ATTACH_JS,
            {
                "channel": manifest.expected_channel,
                "file": {"name": "final.mp4", "base64": encoded},
                "sha256": manifest.video_sha256,
                "sentinel": draft_sentinel,
                "session_marker": session_marker,
            },
            cwd=manifest.video.parent,
            timeout=600,
        )
        count = int(raw.get("provider_observed_attachment_click_count") or 0)
        checks = (
            raw.get("status") == "attached",
            raw.get("account") == ACCOUNT,
            raw.get("headless") is True,
            raw.get("channel") == manifest.expected_channel,
            count == 1,
            raw.get("attachment_name") == "final.mp4",
            raw.get("attachment_sha256") == manifest.video_sha256,
            raw.get("draft_sentinel") == draft_sentinel,
            raw.get("displayed_filename") is True,
            raw.get("upload_flow_preserved") is True,
            raw.get("session_marker") == session_marker,
            bool(VIDEO_ID_RE.fullmatch(str(raw.get("provider_id") or ""))),
        )
        if not all(checks):
            raise publisher.AmbiguousProviderState(
                "Aside did not return one exact provider-observed final.mp4 attachment receipt"
            )
        return dict(raw)

    @staticmethod
    def _provider_date(slot: datetime) -> str:
        return f"{slot.year}. {slot.month}. {slot.day}."

    @staticmethod
    def _provider_time(slot: datetime) -> str:
        marker = "오전" if slot.hour < 12 else "오후"
        return f"{marker} {slot.hour % 12 or 12}:{slot.minute:02d}"

    def schedule_once(
        self,
        manifest: publisher.PublishManifest,
        row: publisher.ProviderRow,
        slot: datetime,
    ) -> Mapping[str, Any]:
        _require_exact_channel(manifest)
        if not row.provider_id or row.identity != row.provider_id:
            raise publisher.ScheduleControlUnavailable(
                "exact draft provider ID is unavailable; do not mutate"
            )
        if slot.tzinfo is None or getattr(slot.tzinfo, "key", None) != "Asia/Seoul":
            raise publisher.PublisherSafetyError("schedule slot must use Asia/Seoul")
        raw = self._run(
            SCHEDULE_JS,
            {
                "channel": manifest.expected_channel,
                "identity": row.identity,
                "provider_id": row.provider_id,
                "sentinel": manifest.draft_sentinel,
                "title": manifest.title,
                "description": manifest.description,
                "urls": list(manifest.canonical_urls),
                "slot": slot.isoformat(),
                "provider_date": self._provider_date(slot),
                "provider_time": self._provider_time(slot),
                "timezone_label": "서울(GMT+09:00)",
            },
            cwd=manifest.video.parent,
            timeout=480,
        )
        count = int(raw.get("provider_observed_schedule_click_count") or 0)
        if (
            raw.get("status") == "blocked"
            and count == 0
            and "direct-visibility-cardinality:0" in str(raw.get("error") or "")
        ):
            raise publisher.ScheduleControlUnavailable(
                "exact upload flow is absent and the direct draft has no visibility control"
            )
        if (
            raw.get("status") != "committed"
            or count != 1
            or raw.get("provider_id") != row.provider_id
            or raw.get("scheduled_at") != slot.isoformat()
            or publisher._editor_text(str(raw.get("title") or ""))
            != publisher._editor_text(manifest.title)
            or publisher._editor_text(str(raw.get("description") or ""))
            != publisher._editor_text(manifest.description)
            or raw.get("no_kids") is not True
            or raw.get("timezone") != "Asia/Seoul"
            or raw.get("flow") not in {"upload", "direct"}
        ):
            raise publisher.AmbiguousProviderState(
                "Aside schedule receipt is not the exact one-click provider observation"
            )
        return dict(raw)

    def direct_requery(
        self, manifest: publisher.PublishManifest, provider_id: str
    ) -> Mapping[str, Any]:
        _require_exact_channel(manifest)
        if not VIDEO_ID_RE.fullmatch(provider_id):
            raise AdapterEvidenceError("direct requery provider ID is invalid")
        raw = self._run(
            DIRECT_JS,
            {
                "channel": manifest.expected_channel,
                "provider_id": provider_id,
                "urls": list(manifest.canonical_urls),
            },
            cwd=manifest.video.parent,
            timeout=180,
        )
        if raw.get("status") != "pass":
            raise AdapterEvidenceError(
                f"direct Studio requery failed: {raw.get('error') or raw.get('status')}"
            )
        scheduled_at = _exact_kst_iso(
            raw.get("scheduled_at"),
            date_text=str(raw.get("schedule_date") or ""),
            time_text=str(raw.get("schedule_time") or ""),
        )
        captured = self._clock()
        result = {
            "provider_id": str(raw.get("provider_id") or ""),
            "shorts_url": str(raw.get("shorts_url") or ""),
            "exact_row_count": raw.get("exact_row_count"),
            "title": str(raw.get("title") or ""),
            "description": str(raw.get("description") or ""),
            "original_urls": list(raw.get("original_urls") or []),
            "no_kids": raw.get("no_kids"),
            "status": str(raw.get("provider_status") or ""),
            "scheduled_at": scheduled_at,
            "timezone": "Asia/Seoul"
            if _timezone_contract_present(raw.get("timezone_evidence"))
            else "",
            "account": raw.get("account"),
            "headless": raw.get("headless"),
            "channel": raw.get("channel"),
            "captured_at": captured.astimezone(KST).isoformat(),
            "query_id": str(uuid.uuid4()),
        }
        if result["provider_id"] != provider_id:
            raise AdapterEvidenceError("direct Studio requery returned another provider ID")
        return result


class AimaxCrmTrackPort:
    """PII-minimal dedupe lookup plus the real tracker emit command."""

    _ALLOWED_KEYS = frozenset(
        {"channel", "campaign", "stage", "status", "count", "dedupe_key"}
    )

    def __init__(
        self,
        *,
        tracker: Path = TRACKER,
        db_path: Path = CRM_DB,
        command_runner: CommandRunner = subprocess.run,
    ):
        self.tracker = Path(tracker)
        self.db_path = Path(db_path)
        self.command_runner = command_runner

    def require_live_dependencies(self) -> None:
        if not self.tracker.is_file() or not os.access(self.tracker, os.X_OK):
            raise LiveDependencyError(f"aimax-crm-track is unavailable: {self.tracker}")
        if not self.db_path.is_file():
            raise LiveDependencyError(f"CRM dedupe ledger is unavailable: {self.db_path}")

    def lookup(self, dedupe_key: str) -> Mapping[str, Any] | None:
        self.require_live_dependencies()
        if not dedupe_key or re.search(r"https?://|@|\s", dedupe_key):
            raise publisher.PublisherSafetyError("CRM dedupe key contains unsafe content")
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT dedupe_key,status,channel,campaign,stage,metric_count "
                    "FROM events WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise LiveDependencyError("CRM dedupe lookup failed") from exc
        return dict(row) if row is not None else None

    def emit(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.require_live_dependencies()
        if set(payload) != self._ALLOWED_KEYS:
            raise publisher.PublisherSafetyError(
                "CRM payload must contain only channel/campaign/stage/status/count/dedupe_key"
            )
        expected = {
            "channel": "youtube_shorts",
            "campaign": "youtube-content-repurpose",
            "stage": "sent",
            "status": "success",
            "count": 1,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise publisher.PublisherSafetyError("CRM payload differs from the sealed sent event")
        dedupe_key = str(payload.get("dedupe_key") or "")
        if not dedupe_key.startswith("external-") or re.search(r"https?://|@|\s", dedupe_key):
            raise publisher.PublisherSafetyError("CRM dedupe key is invalid")
        command = [
            str(self.tracker),
            "emit",
            "--source-system",
            "codex",
            "--channel",
            "youtube_shorts",
            "--stage",
            "sent",
            "--status",
            "success",
            "--origin-agent",
            "codex",
            "--project",
            "navercafe",
            "--campaign",
            "youtube-content-repurpose",
            "--count",
            "1",
            "--dedupe-key",
            dedupe_key,
        ]
        completed = self.command_runner(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        if completed.returncode != 0:
            raise LiveDependencyError("aimax-crm-track emit failed")
        try:
            receipt = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LiveDependencyError("aimax-crm-track returned a non-JSON receipt") from exc
        if receipt.get("ok") is not True:
            raise LiveDependencyError("aimax-crm-track did not confirm the emit")
        return {
            "status": "success",
            "dedupe_key": dedupe_key,
            "channel": "youtube_shorts",
            "campaign": "youtube-content-repurpose",
            "stage": "sent",
            "count": 1,
            "created": receipt.get("created"),
            "event_id": receipt.get("event_id"),
        }


def dry_validate(manifest_path: str | Path) -> dict[str, Any]:
    """Validate only the manifest/local artifact and static adapter bindings."""

    manifest = publisher.load_manifest(manifest_path)
    local = publisher.validate_local_candidate(manifest)
    _require_exact_channel(manifest)
    if PROVIDER_LOCK != publisher.SHARED_PROVIDER_LOCK:
        raise publisher.PublisherSafetyError("adapter/publisher provider lock mismatch")
    return {
        "status": "pass",
        "mode": "dry-validate",
        "source_key": manifest.source_key,
        "final_mp4_sha256": manifest.video_sha256,
        "local_gate_video_sha256": local.get("video_sha256"),
        "provider_access": False,
        "crm_access": False,
        "provider": {"account": ACCOUNT, "headless": True, "lock": str(PROVIDER_LOCK)},
    }


def run_live(manifest_path: str | Path) -> dict[str, Any]:
    """Run the final publisher; dependency checks occur before provider access."""

    manifest = publisher.load_manifest(manifest_path)
    _require_exact_channel(manifest)
    provider_port = AsideHeadlessU0Provider()
    crm_port = AimaxCrmTrackPort()
    crm_port.require_live_dependencies()
    # Resolve only; no Studio access occurs until the publisher has passed its
    # local candidate gate and acquired the shared lock.
    provider_port._runner = _default_aside_runner()
    runner = publisher.YouTubeShortsPublisher(
        provider_port,
        crm_port,
        lock_path=PROVIDER_LOCK,
    )
    return runner.run(manifest_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed YouTube Shorts publisher through Aside headless u0"
    )
    parser.add_argument("manifest", help="publisher manifest JSON")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-validate",
        action="store_true",
        help="validate local/static bindings with no Aside, Studio, or CRM access",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="run the real provider publisher after dependency checks",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dry_validate(args.manifest) if args.dry_validate else run_live(args.manifest)
    except publisher.PublisherSafetyError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCOUNT",
    "HEADLESS",
    "PROVIDER_LOCK",
    "AimaxCrmTrackPort",
    "AsideHeadlessU0Provider",
    "AdapterEvidenceError",
    "LiveDependencyError",
    "dry_validate",
    "run_live",
]
