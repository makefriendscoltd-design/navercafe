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
from datetime import date, datetime, timedelta
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
INVENTORY_DIRECT_CHUNK_SIZE = 8
# Keep provider inspection sequential: even eight concurrent edit tabs lost
# their execution contexts in the real u0 scan on 2026-09-06.
INVENTORY_DIRECT_CONCURRENCY = 1
INVENTORY_CHUNK_MAX_AGE = timedelta(minutes=5)
INVENTORY_CHUNK_FUTURE_SKEW = timedelta(minutes=1)


class LiveDependencyError(publisher.PublisherSafetyError):
    """A live-only executable, login context, or response is unavailable."""


class AdapterEvidenceError(publisher.InventoryError):
    """Aside returned evidence that cannot satisfy the publisher contract."""


AsideRunner = Callable[..., Mapping[str, Any]]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _compose_provider_javascript(
    js_common: str,
    payload_expression: str,
    body: str,
) -> str:
    """Compose one provider program without sharing lexical names with JS_COMMON.

    ``run_repl`` evaluates the returned program inside an async function.  A
    nested block keeps task-local ``let``/``const`` declarations out of the
    common helper scope while preserving access to ``payload``, ``sleep``, and
    ``emit``.  This prevents a selector adapter from failing before execution
    merely because it chose the same helper name as ``JS_COMMON``.
    """

    return (
        js_common
        + "\nconst payload="
        + payload_expression
        + ";\n{\n"
        + body
        + "\n}\n"
    )


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
        code = _compose_provider_javascript(
            JS_COMMON,
            _payload_expression(dict(payload)),
            body,
        )
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


INVENTORY_SEED_JS = r"""
let list=null;
const pages=[],all=[],seen=new Set(),states=['public','scheduled','private','draft'];
const counts={public:0,scheduled:0,private:0,draft:0};
const visible=el=>{if(!el)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
const context=async(p,mode)=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(({channel,channelId,mode})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),channelHrefIds=[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))],channelIdExact=channelHrefIds.length===1&&channelHrefIds[0]===channelId,channelVerified=lines.includes(channel)||channelIdExact,avatar=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),title=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),description=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis),rows=[...document.querySelectorAll('ytcp-video-row')],next=[...document.querySelectorAll('#navigate-after,ytcp-icon-button#navigate-after')];return{host,path,signinRedirect,channelExact:lines.includes(channel),channelIdExact,channelHrefIds,avatarCount:avatar.length,titleCount:title.length,descriptionCount:description.length,rowCount:rows.length,nextCount:next.length,ready:host==='studio.youtube.com'&&!signinRedirect&&channelVerified&&avatar.length===1&&(mode==='edit'?title.length===1&&description.length===1:rows.length>0&&next.length===1)};},{channel:payload.channel,channelId:payload.channel_id,mode});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error(`login-required:${state.host}${state.path}`);if(!state.ready)throw new Error(`studio-context-unverified:${mode}:${JSON.stringify(state)}`);return state;};
const one=async(root,selector,label,{mustBeVisible=true}={})=>{const loc=root.locator(selector);const count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const statusOf=text=>{const lines=text.split('\n').map(x=>x.trim()).filter(Boolean);if(lines.some(x=>/^(예약됨|Scheduled)$/i.test(x)))return 'scheduled';if(lines.some(x=>/^(비공개|Private)$/i.test(x)))return 'private';if(lines.some(x=>/^(공개|Public)$/i.test(x)))return 'public';if(lines.some(x=>/^(초안|Draft)$/i.test(x)))return 'draft';return '';};
const rowOf=async(row,page,rowIndex)=>{const text=((await row.innerText())||'').trim(),titleNodes=row.locator('#video-title'),titleCount=await titleNodes.count();if(titleCount!==1)throw new Error(`row-title-cardinality:${page}:${rowIndex}:${titleCount}`);if(!await titleNodes.isVisible())throw new Error(`row-title-not-visible:${page}:${rowIndex}`);const titleNode=titleNodes,title=(((await titleNode.getAttribute('title'))||(await titleNode.innerText())||'').trim()),hrefs=await row.locator('a[href]').evaluateAll(nodes=>nodes.map(a=>a.href)),images=row.locator('img'),imageCount=await images.count(),src=imageCount?((await images.nth(0).getAttribute('src'))||''):'';const id=([...hrefs,src].join('\n').match(/(?:\/video\/|[?&]v=|\/shorts\/|\/vi\/)([A-Za-z0-9_-]{11})/)||[])[1]||'',status=statusOf(text),dateNodes=row.locator('.tablecell-date'),dateCount=await dateNodes.count();if(dateCount!==1)throw new Error(`row-date-cardinality:${page}:${rowIndex}:${dateCount}`);if(!await dateNodes.isVisible())throw new Error(`row-date-not-visible:${page}:${rowIndex}`);const publishedRaw=((await dateNodes.getAttribute('datetime'))||(await dateNodes.getAttribute('title'))||(await dateNodes.innerText())||'').trim();if(!id)throw new Error(`provider-id-missing:${page}:${rowIndex}`);if(!status)throw new Error(`unknown-state:${id}`);return {identity:id,provider_id:id,status,title,listText:text,urls:hrefs,page,publishedRaw};};
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
 emit({status:'pass',inventory_mode:'seed',seed_phase:payload.seed_phase,account:'u0',headless:true,channel:payload.channel,scan_token:payload.scan_token,chunk_nonce:payload.chunk_nonce,captured_at:new Date().toISOString(),pagination_complete:true,terminal_reason:'next_disabled',pages_scanned:pages.length,pages,scanned_states:states,status_counts:counts,seed_rows:all});
}catch(error){emit({status:'blocked',inventory_mode:'seed',seed_phase:payload.seed_phase,account:'u0',headless:true,channel:payload.channel,scan_token:payload.scan_token,chunk_nonce:payload.chunk_nonce,captured_at:new Date().toISOString(),pagination_complete:false,terminal_reason:'error',pages_scanned:pages.length,pages,scanned_states:states,status_counts:counts,seed_rows:all,error:String(error?.message||error)});}finally{try{if(list)await list.close();}catch(_){}}
"""


INVENTORY_DIRECT_JS = r"""
const states=['public','scheduled','private','draft'];
const one=async(root,selector,label,{mustBeVisible=true}={})=>{const loc=root.locator(selector);const count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const context=async p=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(({channel,channelId})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),channelHrefIds=[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))],channelIdExact=channelHrefIds.length===1&&channelHrefIds[0]===channelId,channelVerified=lines.includes(channel)||channelIdExact,avatar=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),title=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),description=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),channelIdExact,channelHrefIds,avatarCount:avatar.length,titleCount:title.length,descriptionCount:description.length,ready:host==='studio.youtube.com'&&!signinRedirect&&channelVerified&&avatar.length===1&&title.length===1&&description.length===1};},{channel:payload.channel,channelId:payload.channel_id});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error(`login-required:${state.host}${state.path}`);if(!state.ready)throw new Error(`studio-context-unverified:edit:${JSON.stringify(state)}`);return state;};
try{
 const chunkStart=Number(payload.chunk_start),chunkEnd=Number(payload.chunk_end),chunkIndex=Number(payload.chunk_index),chunkTotal=Number(payload.chunk_total),expectedIdentities=payload.expected_identities,directChunk=payload.expected_rows,concurrency=Number(payload.concurrency);
 if(!Number.isInteger(chunkStart)||!Number.isInteger(chunkEnd)||chunkStart<0||chunkEnd<=chunkStart)throw new Error('inventory-chunk-range-invalid');
 if(!Number.isInteger(chunkIndex)||!Number.isInteger(chunkTotal)||chunkIndex<0||chunkTotal<1||chunkIndex>=chunkTotal)throw new Error('inventory-chunk-index-invalid');
 if(!Number.isInteger(concurrency)||concurrency<1||concurrency>16)throw new Error('inventory-chunk-concurrency-invalid');
 if(!Array.isArray(expectedIdentities)||!Array.isArray(directChunk)||directChunk.length!==chunkEnd-chunkStart)throw new Error('inventory-chunk-input-missing');
 const directIdentities=directChunk.map(row=>row&&row.identity);if(JSON.stringify(directIdentities)!==JSON.stringify(expectedIdentities))throw new Error('inventory-chunk-identities-changed');
 if(new Set(directIdentities).size!==directIdentities.length||directChunk.some(row=>!row||row.identity!==row.provider_id||!states.includes(row.status)||!Number.isInteger(row.page)||row.page<1||typeof row.title!=='string'||typeof row.publishedRaw!=='string'||!Array.isArray(row.urls)||row.urls.some(url=>typeof url!=='string')))throw new Error('inventory-chunk-seeds-invalid');
 const uploadFlowIds=new Set(); // Recover only through exact provider routes in the approved headless session.
 const inspect=async seed=>{let edit=null;try{edit=await openTab(`https://studio.youtube.com/video/${seed.provider_id}/edit?strict_metadata=${Date.now()}`);await context(edit);const route=(edit.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'';if(route!==seed.provider_id)throw new Error(`direct-route-mismatch:${seed.provider_id}`);const titleBox=await one(edit,'#title-textarea #textbox','metadata-title'),descriptionBox=await one(edit,'#description-textarea #textbox','metadata-description'),title=((await titleBox.innerText())||'').trim(),description=((await descriptionBox.innerText())||'').trim();if(title!==seed.title)throw new Error(`metadata-title-drift:${seed.provider_id}`);const noKidsSet=edit.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'),visibilitySet=edit.locator('ytcp-video-metadata-visibility #container'),visibilityCount=await visibilitySet.count();if(visibilityCount>1)throw new Error(`visibility-control-cardinality:${seed.provider_id}:${visibilityCount}`);let scheduled_at='',scheduleDate='',scheduleTime='',timezoneEvidence='';if(seed.status==='scheduled'){if(visibilityCount!==1||!await visibilitySet.isVisible())throw new Error(`schedule-visibility-missing:${seed.provider_id}`);await visibilitySet.click();await sleep(450);const date=await one(edit,'#datepicker-trigger','schedule-date'),timeInput=await one(edit,'#time-of-day-container input','schedule-time'),tz=await one(edit,'#timezone-select-button,#timezone-select-trigger','schedule-timezone');scheduleDate=((await date.innerText())||'').replace(/\s+/g,' ').trim();scheduleTime=await timeInput.inputValue();timezoneEvidence=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(!timezoneEvidence)throw new Error(`schedule-timezone-empty:${seed.provider_id}`);}const noKidsCount=await noKidsSet.count();if(noKidsCount>1)throw new Error(`no-kids-cardinality:${seed.provider_id}:${noKidsCount}`);const seedUrls=[...seed.urls];return {...seed,list_title:seed.title,seed_urls:seedUrls,title,description,urls:[...new Set([...seedUrls,...((description.match(/https?:\/\/[^\s]+/g))||[])])],direct_metadata_inspected:true,visibility_control_present:visibilityCount===1||uploadFlowIds.has(seed.provider_id),upload_flow_present:uploadFlowIds.has(seed.provider_id),scheduled_at,schedule_date:scheduleDate,schedule_time:scheduleTime,timezone_evidence:timezoneEvidence,no_kids:noKidsCount===1&&(await noKidsSet.getAttribute('aria-checked'))==='true',published_at:seed.status==='public'?seed.publishedRaw:''};}finally{try{if(edit)await edit.close();}catch(_){}}};
 const rows=[];for(let offset=0;offset<directChunk.length;offset+=concurrency){const settled=await Promise.allSettled(directChunk.slice(offset,offset+concurrency).map(inspect)),failed=settled.find(item=>item.status==='rejected');if(failed)throw failed.reason;rows.push(...settled.map(item=>item.value));}
 emit({status:'pass',inventory_mode:'direct',account:'u0',headless:true,channel:payload.channel,scan_token:payload.scan_token,chunk_nonce:payload.chunk_nonce,chunk_index:chunkIndex,chunk_total:chunkTotal,chunk_start:chunkStart,chunk_end:chunkEnd,expected_identities:expectedIdentities,captured_at:new Date().toISOString(),rows});
}catch(error){emit({status:'blocked',inventory_mode:'direct',account:'u0',headless:true,channel:payload.channel,scan_token:payload.scan_token,chunk_nonce:payload.chunk_nonce,chunk_index:payload.chunk_index,chunk_total:payload.chunk_total,chunk_start:payload.chunk_start,chunk_end:payload.chunk_end,expected_identities:payload.expected_identities,captured_at:new Date().toISOString(),rows:[],error:String(error?.message||error)});}
"""


# Backward-compatible name for code that inspects the exhaustive list program.
INVENTORY_JS = INVENTORY_SEED_JS


ATTACH_JS = r"""
let p=null,attachActions=0,success=false;const compact=s=>(s||'').replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').replace(/\u00A0/g,' ').replace(/\r\n?/g,'\n').replace(/\s+/g,'');
const waitContext=async()=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(({channel,channelId})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),channelHrefIds=[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))],channelIdExact=channelHrefIds.length===1&&channelHrefIds[0]===channelId,channelVerified=lines.includes(channel)||channelIdExact,avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),channelIdExact,channelHrefIds,avatarCount:avatars.length,ready:host==='studio.youtube.com'&&!signinRedirect&&channelVerified&&avatars.length===1};},{channel:payload.channel,channelId:payload.channel_id});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${JSON.stringify(state)}`);};
const exact=async(root,selector,label,{mustBeVisible=true}={})=>{const loc=root.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
try{p=await openTab(`https://studio.youtube.com/?shorts_upload_session=${payload.session_marker}`);await waitContext();const clickUpload=()=>p.evaluate(()=>{const vis=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const found=[...document.querySelectorAll('button,[role="button"],ytcp-button,ytcp-icon-button,tp-yt-paper-item')].filter(e=>vis(e)&&(e.id==='upload-icon'||/동영상 업로드|Upload videos/i.test((e.getAttribute('aria-label')||'')+' '+(e.innerText||''))));if(found.length!==1)return false;found[0].click();return true;});let opened=await clickUpload();if(!opened){const createSet=p.getByText(/^(만들기|Create)$/),createCount=await createSet.count();if(createCount!==1||!await createSet.isVisible())throw new Error(`create-control-cardinality:${createCount}`);await createSet.click();await sleep(400);opened=await clickUpload();}if(!opened)throw new Error('upload-control-missing');await sleep(700);const input=await exact(p,'input[type="file"][name="Filedata"],input[type="file"]','file-input',{mustBeVisible:false});if(payload.file.name!=='final.mp4'||!payload.file.base64)throw new Error('candidate-payload-invalid');attachActions++;await input.setInputFiles([{name:payload.file.name,mimeType:'video/mp4',buffer:Buffer.from(payload.file.base64,'base64')}]);const dialogEnd=Date.now()+120000;let dialogSet=p.locator('ytcp-uploads-dialog');while(Date.now()<dialogEnd&&(await dialogSet.count()!==1||await dialogSet.locator('#title-textarea #textbox').count()!==1||!await dialogSet.locator('#title-textarea #textbox').isVisible()))await sleep(500);const dialog=await exact(p,'ytcp-uploads-dialog','upload-dialog',{mustBeVisible:false}),title=await exact(dialog,'#title-textarea #textbox','upload-title');await title.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await title.pressSequentially(payload.sentinel,{delay:1});if(compact(await title.innerText())!==compact(payload.sentinel))throw new Error('sentinel-fill-mismatch');let body='',providerId='',observed=false;const observedEnd=Date.now()+180000;while(Date.now()<observedEnd&&!observed){body=await dialog.innerText();const values=await dialog.locator('a[href],input').evaluateAll(nodes=>nodes.map(x=>x.href||x.value||'')),badges=dialog.locator('#step-badge-3');providerId=([...values,body].join('\n').match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/video\/)([A-Za-z0-9_-]{11})/)||[])[1]||'';observed=body.includes('final.mp4')&&!!providerId&&await badges.count()===1;if(!observed)await sleep(700);}if(!observed||attachActions!==1)throw new Error('provider-attachment-or-visibility-flow-receipt-missing');success=true;emit({status:'attached',account:'u0',headless:true,channel:payload.channel,provider_observed_attachment_click_count:attachActions,attachment_name:'final.mp4',attachment_sha256:payload.sha256,draft_sentinel:payload.sentinel,provider_id:providerId,displayed_filename:true,upload_flow_preserved:true,session_marker:payload.session_marker});}catch(error){let diagnostic={};try{diagnostic=await p.evaluate(()=>({url:location.origin+location.pathname,dialog_count:document.querySelectorAll('ytcp-uploads-dialog').length,title_count:document.querySelectorAll('ytcp-uploads-dialog #title-textarea #textbox').length,upload_text:(document.querySelector('ytcp-uploads-dialog')?.innerText||'').slice(0,6000),files:[...document.querySelectorAll('input[type=file]')].flatMap(x=>[...x.files].map(f=>({name:f.name,size:f.size,type:f.type})))}));}catch(_){}emit({status:'blocked',account:'u0',headless:true,channel:payload.channel,provider_observed_attachment_click_count:attachActions,diagnostic,error:String(error?.message||error)});}finally{try{if(p&&attachActions===0)await p.close();}catch(_){}}
"""


SAVE_ATTACHED_METADATA_JS = r"""
let p=null,saveClicks=0;
const read=async()=>({title:((await p.locator('#title-textarea #textbox').innerText())||'').trim(),description:((await p.locator('#description-textarea #textbox').innerText())||'').trim()});
const openExact=async()=>{p=await openTab('https://studio.youtube.com/video/'+payload.id+'/edit');const end=Date.now()+30000;let ready=false;while(Date.now()<end&&!ready){const body=await p.locator('body').innerText();ready=p.url().includes('/video/'+payload.id+'/edit')&&body.includes(payload.channel)&&body.includes('final.mp4')&&await p.locator('#title-textarea #textbox').count()===1&&await p.locator('#description-textarea #textbox').count()===1;if(!ready)await sleep(250);}if(!ready)throw Error('exact-attached-video-binding-failed');};
try{await openExact();const before=await read();if(!['final',payload.sentinel,payload.title].includes(before.title)||!['',payload.description.trim()].includes(before.description))throw Error('unexpected-attached-metadata');if(before.title!==payload.title||before.description!==payload.description.trim()){for(const [selector,value] of [['#title-textarea #textbox',payload.title],['#description-textarea #textbox',payload.description]]){const loc=p.locator(selector);if(await loc.count()!==1)throw Error('metadata-cardinality');await loc.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await loc.pressSequentially(value,{delay:1});await p.keyboard.press('Tab');if((await loc.innerText()).trim()!==value.trim())throw Error('metadata-entry-mismatch');}const save=p.locator('ytcp-button#save');if(await save.count()!==1||!await save.isEnabled())throw Error('metadata-save-unavailable');saveClicks++;await save.click();const end=Date.now()+30000;while(Date.now()<end&&await save.isEnabled())await sleep(250);if(await save.isEnabled())throw Error('metadata-save-unconfirmed');const toast=p.getByText('변경사항이 저장됨',{exact:true}),savedEnd=Date.now()+30000;while(Date.now()<savedEnd&&(await toast.count()!==1||!await toast.isVisible()))await sleep(200);if(await toast.count()!==1||!await toast.isVisible())throw Error('metadata-saved-notification-missing');}await p.close();p=null;await openExact();const after=await read();if(after.title!==payload.title||after.description!==payload.description.trim())throw Error('persisted-metadata-mismatch');emit({status:'pass',provider_id:payload.id,save_clicks:saveClicks,title:after.title,description:after.description,fresh_read_verified:true});}catch(error){emit({status:'blocked',provider_id:payload.id,save_clicks:saveClicks,error:String(error?.message||error)});}finally{try{if(p)await p.close();}catch(_){}}
"""


SCHEDULE_JS = r"""
let p=null,root=null,scheduleClicks=0,flow='';const compact=s=>(s||'').replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').replace(/\u00A0/g,' ').replace(/\r\n?/g,'\n').replace(/\s+/g,'');
const exact=async(base,selector,label,{mustBeVisible=true}={})=>{const loc=base.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(mustBeVisible&&!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const context=async(page,mode)=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await page.evaluate(({channel,channelId,mode})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),channelHrefIds=[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))],channelIdExact=channelHrefIds.length===1&&channelHrefIds[0]===channelId,channelVerified=lines.includes(channel)||channelIdExact,avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),titles=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),descriptions=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),channelIdExact,channelHrefIds,avatarCount:avatars.length,titleCount:titles.length,descriptionCount:descriptions.length,ready:host==='studio.youtube.com'&&!signinRedirect&&channelVerified&&avatars.length===1&&(mode==='edit'?titles.length===1&&descriptions.length===1:true)};},{channel:payload.channel,channelId:payload.channel_id,mode});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${mode}:${JSON.stringify(state)}`);};
const fill=async(loc,value)=>{await loc.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');await loc.pressSequentially(value,{delay:1});return compact(await loc.innerText())===compact(value);};
try{if(payload.identity!==payload.provider_id)throw new Error('exact-draft-identity-mismatch');
p=await openTab(`https://studio.youtube.com/video/${payload.provider_id}/edit?exact_draft_recovery=${Date.now()}`);await context(p,'edit');
const route=(p.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'';if(route!==payload.provider_id)throw new Error('exact-draft-route-mismatch');
let readyTitle='';for(let i=0;i<100;i++){readyTitle=((await p.locator('#title-textarea #textbox').innerText())||'').trim();if([payload.sentinel,payload.title].includes(readyTitle))break;await sleep(200);}if(![payload.sentinel,payload.title].includes(readyTitle))throw new Error('draft-sentinel-mismatch');
const editDraft=p.getByText('초안 수정',{exact:true});root=p;flow='direct';
if(await editDraft.count()===1&&await editDraft.isVisible()){await editDraft.click();const end=Date.now()+15000;while(Date.now()<end){const dialogs=p.locator('ytcp-uploads-dialog');if(await dialogs.count()===1&&await dialogs.locator('#title-textarea #textbox').count()===1&&await dialogs.locator('#title-textarea #textbox').isVisible()){root=dialogs;flow='upload';break;}await sleep(200);}if(flow!=='upload')throw new Error('draft-wizard-not-ready');}

 const title=await exact(root,'#title-textarea #textbox','metadata-title'),description=await exact(root,'#description-textarea #textbox','metadata-description'),beforeTitle=((await title.innerText())||'').trim();if(![payload.sentinel,payload.title].includes(beforeTitle))throw new Error('draft-sentinel-mismatch');if((((await title.innerText())||'').trim()!==payload.title&&!await fill(title,payload.title))||(((await description.innerText())||'').trim()!==payload.description&&!await fill(description,payload.description)))throw new Error('metadata-fill-mismatch');let noKidsSet=root.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');if(await noKidsSet.count()===0){const expandSet=root.locator('#second-container-expand-button');if(await expandSet.count()===1&&await expandSet.isVisible()){await expandSet.click();await sleep(400);}noKidsSet=root.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');}if(await noKidsSet.count()!==1||!await noKidsSet.isVisible())throw new Error(`no-kids-control-cardinality:${await noKidsSet.count()}`);if((await noKidsSet.getAttribute('aria-checked'))!=='true'){await noKidsSet.click();await sleep(350);}if((await noKidsSet.getAttribute('aria-checked'))!=='true')throw new Error('no-kids-not-selected');
const landedTitle=((await title.innerText())||'').trim(),landedDescription=((await description.innerText())||'').trim();
if(flow==='upload'){const badge=await exact(root,'#step-badge-3','visibility-step');if(!await badge.isEnabled())throw new Error('visibility-step-not-ready');await badge.click();await sleep(900);}else{const visibility=await exact(root,'ytcp-video-metadata-visibility #container','direct-visibility');await visibility.click();await sleep(600);}
const scheduleSelector=flow==='upload'?'#second-container':'tp-yt-paper-radio-button[name="SCHEDULE"],[role="radio"][name="SCHEDULE"],#publish-from-private-non-sponsor-selector',schedule=await exact(root,scheduleSelector,'schedule-selector');if(flow==='upload'||(await schedule.getAttribute('aria-checked'))!=='true'){await schedule.click();await sleep(650);}let tz=await exact(root,'#timezone-select-button,#timezone-select-trigger','timezone-control');await tz.click();await sleep(350);const zones=p.getByText(payload.timezone_label,{exact:true}),zoneCount=await zones.count();if(zoneCount!==1||!await zones.isVisible())throw new Error(`timezone-option-cardinality:${zoneCount}`);await zones.click();await sleep(650);let dateControl=await exact(root,'#datepicker-trigger','schedule-date'),timeControl=await exact(root,'#time-of-day-container input','schedule-time');await dateControl.click();await sleep(250);const dateInput=await exact(p,'ytcp-date-picker input','date-input');await dateInput.fill(payload.provider_date);await dateInput.press('Enter');await sleep(450);timeControl=await exact(root,'#time-of-day-container input','schedule-time-after-date');await timeControl.fill(payload.provider_time);await timeControl.press('Tab');let selectedDate='',selectedTime='',timezoneLabel='';const stableEnd=Date.now()+12000;while(Date.now()<stableEnd){dateControl=await exact(root,'#datepicker-trigger','schedule-date-after-fill');timeControl=await exact(root,'#time-of-day-container input','schedule-time-after-fill');tz=await exact(root,'#timezone-select-button,#timezone-select-trigger','timezone-after-fill');selectedDate=((await dateControl.innerText())||'').replace(/\s+/g,' ').trim();selectedTime=await timeControl.inputValue();timezoneLabel=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(selectedDate===payload.provider_date&&selectedTime===payload.provider_time&&timezoneLabel)break;await sleep(300);}if(landedTitle!==payload.title||compact(landedDescription)!==compact(payload.description)||!payload.urls.every(url=>landedDescription.includes(url))||selectedDate!==payload.provider_date||selectedTime!==payload.provider_time||!timezoneLabel)throw new Error('schedule-precommit-evidence-mismatch');const done=await exact(root,'#done-button','done-button'),innerButtons=done.locator('button'),innerCount=await innerButtons.count(),doneText=((await done.innerText())||'').trim(),doneAria=innerCount===1?((await innerButtons.getAttribute('aria-label'))||''):((await done.getAttribute('aria-label'))||'');if(innerCount>1||!await done.isEnabled()||(await done.getAttribute('aria-disabled'))==='true'||!/^(예약|Schedule)$/.test(doneText)||!/^(예약|Schedule)$/.test(doneAria))throw new Error('single-schedule-control-not-ready');if(payload.prepare_only){emit({status:'prepared',provider_id:payload.provider_id,title:landedTitle,scheduled_at:payload.slot,flow,provider_observed_schedule_click_count:0});return;}scheduleClicks=1;await done.click();let confirmed=false,body='';const end=Date.now()+90000;while(Date.now()<end&&!confirmed){await sleep(700);body=await p.locator('body').innerText();confirmed=/동영상 예약됨|Video scheduled/i.test(body)||await root.locator('#done-button').count()===0;}if(!confirmed)throw new Error('schedule-confirmation-missing');emit({status:'committed',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,provider_observed_schedule_click_count:scheduleClicks,title:landedTitle,description:landedDescription,no_kids:true,scheduled_at:payload.slot,timezone:'Asia/Seoul',flow});}catch(error){emit({status:scheduleClicks===0?'blocked':'uncertain_after_click_do_not_retry',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,provider_observed_schedule_click_count:scheduleClicks,flow,error:String(error?.message||error)});}finally{try{if(p&&!(flow==='upload'&&scheduleClicks===0))await p.close();}catch(_){}}
"""


DIRECT_JS = r"""
let p=null;const exact=async(base,selector,label)=>{const loc=base.locator(selector),count=await loc.count();if(count!==1)throw new Error(`${label}-cardinality:${count}`);if(!await loc.isVisible())throw new Error(`${label}-not-visible`);return loc;};
const context=async()=>{const end=Date.now()+30000;let state={};while(Date.now()<end){state=await p.evaluate(({channel,channelId})=>{const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';},host=location.hostname,path=location.pathname+location.search,signinRedirect=host==='accounts.google.com'||(host==='studio.youtube.com'&&/(?:^|\/)(?:signin|login)(?:\/|$)/i.test(path)),lines=(document.body?.innerText||'').split('\n').map(x=>x.trim()),channelHrefIds=[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))],channelIdExact=channelHrefIds.length===1&&channelHrefIds[0]===channelId,channelVerified=lines.includes(channel)||channelIdExact,avatars=[...document.querySelectorAll('#avatar-btn,button[aria-label*="계정"],button[aria-label*="Account"]')].filter(vis),titles=[...document.querySelectorAll('#title-textarea #textbox')].filter(vis),descriptions=[...document.querySelectorAll('#description-textarea #textbox')].filter(vis);return{host,path,signinRedirect,channelExact:lines.includes(channel),channelIdExact,channelHrefIds,avatarCount:avatars.length,titleCount:titles.length,descriptionCount:descriptions.length,ready:host==='studio.youtube.com'&&!signinRedirect&&channelVerified&&avatars.length===1&&titles.length===1&&descriptions.length===1};},{channel:payload.channel,channelId:payload.channel_id});if(state.ready||state.signinRedirect)break;await sleep(300);}if(state.signinRedirect)throw new Error('login-required');if(!state.ready)throw new Error(`studio-context-unverified:${JSON.stringify(state)}`);};
try{p=await openTab(`https://studio.youtube.com/video/${payload.provider_id}/edit?strict_direct_requery=${Date.now()}`);await context();const route=(p.url().match(/\/video\/([A-Za-z0-9_-]{11})\/edit/)||[])[1]||'',body=await p.locator('body').innerText();if(route!==payload.provider_id)throw new Error('direct-route-mismatch');const title=await exact(p,'#title-textarea #textbox','direct-title'),description=await exact(p,'#description-textarea #textbox','direct-description'),noKids=await exact(p,'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"],[role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]','direct-no-kids'),visibility=await exact(p,'ytcp-video-metadata-visibility #container','direct-visibility');await visibility.click();await sleep(500);const dateControl=await exact(p,'#datepicker-trigger','direct-date'),timeControl=await exact(p,'#time-of-day-container input','direct-time'),tz=await exact(p,'#timezone-select-button,#timezone-select-trigger','direct-timezone'),titleText=((await title.innerText())||'').trim(),descriptionText=((await description.innerText())||'').trim(),dateText=((await dateControl.innerText())||'').replace(/\s+/g,' ').trim(),timeText=await timeControl.inputValue(),timezone=((await tz.innerText())||'').replace(/\s+/g,' ').trim();if(!timezone)throw new Error('direct-timezone-empty');await p.close();p=null;/* The edit form shows what was typed into it, saved or not. Read the video list too: its row model is the provider's own answer. */p=await openTab(`${payload.list_url}&direct_list_verify=${Date.now()}`);await sleep(9000);let listRowTitle=null;for(let page=1;page<=80&&listRowTitle===null;page++){listRowTitle=await p.evaluate(id=>{const row=[...document.querySelectorAll('ytcp-video-row')].find(e=>e.video?.videoId===id);return row?((row.video?.title||'').trim()):null;},payload.provider_id);if(listRowTitle!==null)break;const next=p.locator('#navigate-after,ytcp-icon-button#navigate-after');if(await next.count()!==1)break;const dis=(await next.getAttribute('aria-disabled'))==='true'||(await next.getAttribute('disabled'))!==null||!await next.isEnabled();if(dis)break;await next.click();await sleep(3500);}if(listRowTitle===null)throw new Error('direct-list-row-missing');emit({status:'pass',account:'u0',headless:true,channel:payload.channel,provider_id:route,shorts_url:`https://www.youtube.com/shorts/${route}`,exact_row_count:1,title:titleText,list_row_title:listRowTitle,description:descriptionText,original_urls:payload.urls.filter(url=>descriptionText.includes(url)),no_kids:(await noKids.getAttribute('aria-checked'))==='true',provider_status:/예약됨|Scheduled/i.test(body)?'scheduled':'unknown',schedule_date:dateText,schedule_time:timeText,timezone_evidence:timezone});}catch(error){emit({status:'blocked',account:'u0',headless:true,channel:payload.channel,provider_id:payload.provider_id,error:String(error?.message||error)});}finally{try{if(p)await p.close();}catch(_){}}
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
        # YouTubeShortsPublisher owns the shared provider lock for this whole
        # method.  Keep every Aside call read-only and bind the bounded calls
        # into one fail-closed logical scan with this token.
        scan_token = uuid.uuid4().hex

        def seed_binding(value: Mapping[str, Any]) -> dict[str, Any]:
            if not isinstance(value, Mapping):
                raise AdapterEvidenceError("inventory seed row is not an object")
            identity = value.get("identity")
            provider_id = value.get("provider_id")
            status = value.get("status")
            page = value.get("page")
            title = value.get("title")
            published_raw = value.get("publishedRaw")
            urls = value.get("urls")
            if (
                not isinstance(identity, str)
                or not VIDEO_ID_RE.fullmatch(identity)
                or provider_id != identity
            ):
                raise AdapterEvidenceError("inventory seed identity binding is invalid")
            if status not in publisher.STATES:
                raise AdapterEvidenceError("inventory seed state is invalid")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise AdapterEvidenceError("inventory seed page is invalid")
            if not isinstance(title, str) or not isinstance(published_raw, str):
                raise AdapterEvidenceError("inventory seed text binding is invalid")
            if not isinstance(urls, list) or any(
                not isinstance(url, str) for url in urls
            ):
                raise AdapterEvidenceError("inventory seed URLs are invalid")
            return {
                "identity": identity,
                "provider_id": provider_id,
                "status": status,
                "page": page,
                "list_title": title,
                "publishedRaw": published_raw,
                "seed_urls": list(urls),
            }

        def call_seed(
            *, seed_phase: str
        ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], datetime]:
            nonce = uuid.uuid4().hex
            raw = self._run(
                INVENTORY_SEED_JS,
                {
                    "list_url": STUDIO_SHORTS_URL,
                    "channel": manifest.expected_channel,
                    "channel_id": CHANNEL_ID,
                    "scan_token": scan_token,
                    "chunk_nonce": nonce,
                    "seed_phase": seed_phase,
                },
                cwd=manifest.video.parent,
                # Stay below the observed daemon disconnect window.  A timeout
                # leaves this read-only scan failed, never partially accepted.
                timeout=100,
            )
            if (
                raw.get("status") != "pass"
                or raw.get("inventory_mode") != "seed"
                or raw.get("seed_phase") != seed_phase
                or raw.get("pagination_complete") is not True
                or raw.get("terminal_reason") != "next_disabled"
            ):
                raise AdapterEvidenceError(
                    "exhaustive Studio inventory seed failed: "
                    f"{raw.get('error') or raw.get('status')}"
                )
            binding = (
                raw.get("account") == ACCOUNT,
                raw.get("headless") is True,
                raw.get("channel") == manifest.expected_channel,
                raw.get("scan_token") == scan_token,
                raw.get("chunk_nonce") == nonce,
            )
            if not all(binding):
                raise AdapterEvidenceError("inventory seed binding differs or is stale")
            try:
                captured = datetime.fromisoformat(
                    str(raw.get("captured_at") or "").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise AdapterEvidenceError("inventory seed timestamp is invalid") from exc
            now = self._clock()
            if captured.tzinfo is None or now.tzinfo is None:
                raise AdapterEvidenceError("inventory seed timestamp must be timezone-aware")
            age = now.astimezone(KST) - captured.astimezone(KST)
            if age >= INVENTORY_CHUNK_MAX_AGE or age < -INVENTORY_CHUNK_FUTURE_SKEW:
                raise AdapterEvidenceError("inventory seed evidence is stale")

            pages = raw.get("pages")
            seeds = raw.get("seed_rows")
            if (
                not isinstance(pages, list)
                or not pages
                or not isinstance(seeds, list)
                or not seeds
            ):
                raise AdapterEvidenceError("inventory seed pages or rows are missing")
            if int(raw.get("pages_scanned") or 0) != len(pages):
                raise AdapterEvidenceError("inventory page cardinality differs")
            if pages[-1].get("next_disabled") is not True:
                raise AdapterEvidenceError(
                    "inventory final page has no disabled-next evidence"
                )
            page_numbers = [int(item.get("page") or 0) for item in pages]
            if page_numbers != list(range(1, len(pages) + 1)):
                raise AdapterEvidenceError("inventory page sequence is not exhaustive")
            seed_bindings = [seed_binding(item) for item in seeds]
            seed_ids = [item["identity"] for item in seed_bindings]
            if len(set(seed_ids)) != len(seed_ids):
                raise AdapterEvidenceError("inventory seed identities are invalid or duplicated")
            if any(item["page"] > len(pages) for item in seed_bindings):
                raise AdapterEvidenceError("inventory seed page is outside the exhaustive range")
            calculated_seed_counts = {
                state: sum(item["status"] == state for item in seed_bindings)
                for state in publisher.STATES
            }
            supplied = raw.get("status_counts") or {}
            if any(
                int(supplied.get(state, -1)) != calculated_seed_counts[state]
                for state in publisher.STATES
            ):
                raise AdapterEvidenceError(
                    "inventory state evidence does not account for every seed row"
                )
            if set(raw.get("scanned_states") or []) != publisher.STATES:
                raise AdapterEvidenceError("inventory did not scan all four provider states")
            for page_number, page in enumerate(pages, 1):
                page_seeds = [
                    item for item in seed_bindings if item["page"] == page_number
                ]
                if int(page.get("row_count") or -1) != len(page_seeds):
                    raise AdapterEvidenceError("inventory page row count differs")
                page_counts = page.get("state_counts") or {}
                if any(
                    int(page_counts.get(state, -1))
                    != sum(item["status"] == state for item in page_seeds)
                    for state in publisher.STATES
                ):
                    raise AdapterEvidenceError("inventory page state counts differ")
            return pages, seeds, captured.astimezone(KST)

        def call_direct(
            *,
            start: int,
            end: int,
            index: int,
            total: int,
            expected_seeds: list[Mapping[str, Any]],
        ) -> tuple[list[Mapping[str, Any]], datetime]:
            nonce = uuid.uuid4().hex
            expected_identities = [
                str(value.get("identity") or "") for value in expected_seeds
            ]
            raw = self._run(
                INVENTORY_DIRECT_JS,
                {
                    "channel": manifest.expected_channel,
                    "channel_id": CHANNEL_ID,
                    "sentinel": manifest.draft_sentinel,
                    "title": manifest.title,
                    "scan_token": scan_token,
                    "chunk_nonce": nonce,
                    "chunk_start": start,
                    "chunk_end": end,
                    "chunk_index": index,
                    "chunk_total": total,
                    "expected_identities": expected_identities,
                    "expected_rows": [dict(value) for value in expected_seeds],
                    "concurrency": INVENTORY_DIRECT_CONCURRENCY,
                },
                cwd=manifest.video.parent,
                timeout=100,
            )
            binding = (
                raw.get("status") == "pass",
                raw.get("inventory_mode") == "direct",
                raw.get("account") == ACCOUNT,
                raw.get("headless") is True,
                raw.get("channel") == manifest.expected_channel,
                raw.get("scan_token") == scan_token,
                raw.get("chunk_nonce") == nonce,
                raw.get("chunk_index") == index,
                raw.get("chunk_total") == total,
                raw.get("chunk_start") == start,
                raw.get("chunk_end") == end,
                raw.get("expected_identities") == expected_identities,
            )
            if not all(binding):
                raise AdapterEvidenceError(
                    "inventory direct chunk failed, differs, or is stale: "
                    f"{raw.get('error') or raw.get('status')}"
                )
            try:
                captured = datetime.fromisoformat(
                    str(raw.get("captured_at") or "").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise AdapterEvidenceError("inventory direct timestamp is invalid") from exc
            now = self._clock()
            if captured.tzinfo is None or now.tzinfo is None:
                raise AdapterEvidenceError(
                    "inventory direct timestamp must be timezone-aware"
                )
            age = now.astimezone(KST) - captured.astimezone(KST)
            if age >= INVENTORY_CHUNK_MAX_AGE or age < -INVENTORY_CHUNK_FUTURE_SKEW:
                raise AdapterEvidenceError("inventory direct evidence is stale")
            rows = raw.get("rows")
            if not isinstance(rows, list):
                raise AdapterEvidenceError("inventory direct chunk rows are missing")
            row_ids = [
                str(item.get("identity") or "")
                for item in rows
                if isinstance(item, Mapping)
            ]
            if row_ids != expected_identities or len(rows) != end - start:
                raise AdapterEvidenceError("inventory direct chunk is missing or reordered")
            for row, expected_seed in zip(rows, expected_seeds, strict=True):
                if not isinstance(row, Mapping):
                    raise AdapterEvidenceError("inventory direct row is not an object")
                expected = seed_binding(expected_seed)
                actual = {
                    "identity": row.get("identity"),
                    "provider_id": row.get("provider_id"),
                    "status": row.get("status"),
                    "page": row.get("page"),
                    "list_title": row.get("list_title"),
                    "publishedRaw": row.get("publishedRaw"),
                    "seed_urls": row.get("seed_urls"),
                }
                if actual != expected:
                    raise AdapterEvidenceError(
                        "inventory direct row differs from its exact seed binding"
                    )
                if row.get("title") != expected["list_title"]:
                    raise AdapterEvidenceError(
                        "inventory direct metadata title differs from the list seed"
                    )
                direct_urls = row.get("urls")
                if not isinstance(direct_urls, list) or any(
                    not isinstance(url, str) for url in direct_urls
                ):
                    raise AdapterEvidenceError("inventory direct URLs are invalid")
                relevant_seed_urls = list(dict.fromkeys(expected["seed_urls"]))
                description = row.get("description")
                if not isinstance(description, str):
                    raise AdapterEvidenceError(
                        "inventory direct description is invalid"
                    )
                expected_direct_urls = list(
                    dict.fromkeys(
                        [
                            *relevant_seed_urls,
                            *re.findall(r"https?://[^\s]+", description),
                        ]
                    )
                )
                if direct_urls != expected_direct_urls:
                    raise AdapterEvidenceError(
                        "inventory direct URLs differ from the list seed"
                    )
                expected_published = (
                    expected["publishedRaw"] if expected["status"] == "public" else ""
                )
                if row.get("published_at") != expected_published:
                    raise AdapterEvidenceError(
                        "inventory direct published evidence differs from the list seed"
                    )
            return rows, captured.astimezone(KST)

        def stable_snapshot(
            pages: list[Mapping[str, Any]], seeds: list[Mapping[str, Any]]
        ) -> str:
            stable_rows = []
            for value in seeds:
                text = str(value.get("listText") or "")
                stable_rows.append(
                    {
                        "identity": str(value.get("identity") or ""),
                        "provider_id": str(value.get("provider_id") or ""),
                        "status": str(value.get("status") or ""),
                        "title": str(value.get("title") or ""),
                        "page": int(value.get("page") or 0),
                        "published_raw": str(value.get("publishedRaw") or ""),
                        "urls": list(value.get("urls") or []),
                        # Preserve duplicate-relevant plain-text URLs while
                        # excluding volatile views/comments metrics.
                        "list_urls": re.findall(r"https?://[^\s]+", text),
                    }
                )
            value = {"pages": pages, "rows": stable_rows}
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        logical_started = self._clock()
        if logical_started.tzinfo is None:
            raise AdapterEvidenceError("inventory start timestamp must be timezone-aware")
        logical_started = logical_started.astimezone(KST)
        pages, seeds, captured = call_seed(seed_phase="initial")
        if captured < logical_started:
            raise AdapterEvidenceError("inventory initial seed predates the logical scan")
        baseline = stable_snapshot(pages, seeds)
        identities = [str(value.get("identity") or "") for value in seeds]
        if INVENTORY_DIRECT_CHUNK_SIZE < 1:
            raise AdapterEvidenceError("inventory direct chunk size must be positive")
        if not 1 <= INVENTORY_DIRECT_CONCURRENCY <= 16:
            raise AdapterEvidenceError("inventory direct concurrency is outside its safety bound")
        total = (len(identities) + INVENTORY_DIRECT_CHUNK_SIZE - 1) // INVENTORY_DIRECT_CHUNK_SIZE
        if total < 1:
            raise AdapterEvidenceError("inventory seed scan returned no rows")

        rows: list[Mapping[str, Any]] = []
        first_captured = captured
        previous_captured = captured
        for index in range(total):
            start = index * INVENTORY_DIRECT_CHUNK_SIZE
            end = min(len(identities), start + INVENTORY_DIRECT_CHUNK_SIZE)
            direct_rows, captured = call_direct(
                start=start,
                end=end,
                index=index,
                total=total,
                expected_seeds=seeds[start:end],
            )
            if captured < previous_captured:
                raise AdapterEvidenceError(
                    "Studio inventory chunk timestamps are out of order"
                )
            if captured - first_captured >= INVENTORY_CHUNK_MAX_AGE:
                raise AdapterEvidenceError(
                    "Studio inventory logical scan exceeded its freshness window"
                )
            previous_captured = captured
            rows.extend(direct_rows)

        final_pages, final_seeds, final_captured = call_seed(seed_phase="final")
        if stable_snapshot(final_pages, final_seeds) != baseline:
            raise AdapterEvidenceError(
                "Studio inventory changed between initial and final seed scans"
            )
        if final_captured < previous_captured:
            raise AdapterEvidenceError("Studio inventory timestamps are out of order")
        if final_captured - logical_started >= INVENTORY_CHUNK_MAX_AGE:
            raise AdapterEvidenceError(
                "Studio inventory logical scan exceeded its freshness window"
            )
        pages, seeds, captured = final_pages, final_seeds, final_captured

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
        if any(
            calculated[state]
            != sum(str(item.get("status") or "") == state for item in seeds)
            for state in publisher.STATES
        ):
            raise AdapterEvidenceError("inventory state evidence does not account for every row")
        return {
            "status": "pass",
            "scan_id": scan_token,
            "phase": phase,
            "captured_at": captured.isoformat(),
            "account": ACCOUNT,
            "headless": True,
            "channel": manifest.expected_channel,
            "channel_id": CHANNEL_ID,
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
        receipt_dir = manifest.video.parent / "provider"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "attachment_invocation.json").write_text(json.dumps({
            "session_marker": session_marker, "video_sha256": manifest.video_sha256,
            "draft_sentinel": draft_sentinel, "status": "started",
        }, ensure_ascii=False, indent=2))
        raw = self._run(
            ATTACH_JS,
            {
                "channel": manifest.expected_channel,
                "channel_id": CHANNEL_ID,
                "file": {"name": "final.mp4", "base64": encoded},
                "sha256": manifest.video_sha256,
                "sentinel": draft_sentinel,
                "session_marker": session_marker,
            },
            cwd=manifest.video.parent,
            timeout=600,
        )
        (receipt_dir / "attachment_receipt.json").write_text(json.dumps(dict(raw), ensure_ascii=False, indent=2))
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
                "channel_id": CHANNEL_ID,
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
        receipt_dir = manifest.video.parent / "provider"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "schedule_receipt.json").write_text(json.dumps(dict(raw), ensure_ascii=False, indent=2))
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
                "channel_id": CHANNEL_ID,
                "provider_id": provider_id,
                "urls": list(manifest.canonical_urls),
                "list_url": STUDIO_SHORTS_URL,
            },
            cwd=manifest.video.parent,
            timeout=300,
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
            "list_row_title": str(raw.get("list_row_title") or ""),
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
    class RowModelProvider(AsideHeadlessU0Provider):
        def attach_once(self, manifest, *, draft_sentinel):
            receipt = super().attach_once(manifest, draft_sentinel=draft_sentinel)
            metadata = self._run(SAVE_ATTACHED_METADATA_JS, {
                "id": receipt["provider_id"], "channel": manifest.expected_channel,
                "title": manifest.title, "description": manifest.description,
                "sentinel": draft_sentinel,
            }, cwd=manifest.video.parent, timeout=150)
            (manifest.video.parent / "provider/attachment_metadata_receipt.json").write_text(
                json.dumps(dict(metadata), ensure_ascii=False, indent=2))
            if (metadata.get("status") != "pass" or metadata.get("fresh_read_verified") is not True
                    or metadata.get("provider_id") != receipt["provider_id"]):
                raise publisher.AmbiguousProviderState("Attached video metadata not persisted; recover exact receipt ID without reupload")
            return receipt

        def scan_inventory(self, manifest, *, phase):
            _require_exact_channel(manifest)
            from youtube_shorts_inventory import scan
            return scan(manifest, phase=phase)

    provider_port = RowModelProvider()
    crm_port = AimaxCrmTrackPort()
    crm_port.require_live_dependencies()
    # Resolve only; no Studio access occurs until the publisher has passed its
    # local candidate gate and acquired the shared lock.
    provider_port._runner = _default_aside_runner()
    runner_class = publisher.YouTubeShortsPublisher
    if manifest.replacement:
        from youtube_shorts_replacement import ReplacementPublisher
        runner_class = ReplacementPublisher
    runner = runner_class(
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
