"""NotebookLM automation through the approved Aside ``u0`` browser profile."""

from __future__ import annotations

import fcntl
import json
import re
import shutil
from pathlib import Path
from typing import Any

from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import ASIDE_ACCOUNT, validate_notebook_binding


PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")


class NotebookLMAsideError(RuntimeError):
    """Raised when the pinned NotebookLM browser workflow cannot be proven."""


def _source_key(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else "source"


def ask_existing_notebook(
    youtube_url: str,
    prompt: str,
    *,
    kind: str,
    notebook_id: str,
    notebook_title: str,
    timeout: int = 300,
    account: str = ASIDE_ACCOUNT,
    evidence_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Ask one pinned notebook with one temporary source and restore its state."""
    if not youtube_url.strip() or not prompt.strip():
        raise ValueError("NotebookLM URL과 프롬프트는 비어 있을 수 없습니다.")
    validate_notebook_binding(
        kind,
        account=account,
        title=notebook_title,
        notebook_id=notebook_id,
    )
    payload = {
        "url": youtube_url.strip(),
        "prompt": prompt.strip(),
        "kind": kind,
        "notebookId": notebook_id,
        "notebookTitle": notebook_title,
        "evidencePrefix": f"notebooklm-{_source_key(youtube_url)}",
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r'''
const p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&aside_pipeline=${Date.now()}`);
await sleep(5000);
const initialUrl=p.url();
const norm=s=>(s||'').normalize('NFKC').replace(/[\u2019\u2018]/g,"'")
  .replace(/^(?:Select|선택)\s+/i,'').replace(/\s+(?:Select|선택)$/i,'')
  .replace(/\s+/g,' ').trim();
const notebookValues=async()=>await p.evaluate(()=>[...document.querySelectorAll('input')]
  .map(x=>(x.value||'').trim()).filter(Boolean));
const sourceState=async()=>await p.evaluate(()=>[...document.querySelectorAll('input[type="checkbox"]')]
  .filter(x=>(x.getAttribute('aria-label')||'')!=='모든 출처 선택')
  .map(x=>({label:x.getAttribute('aria-label')||'',checked:x.checked})));
const labelCounts=values=>{const counts={};for(const value of values)counts[norm(value)]=(counts[norm(value)]||0)+1;return counts;};
const sameCounts=(left,right)=>{const a=labelCounts(left),b=labelCounts(right),keys=new Set([...Object.keys(a),...Object.keys(b)]);return [...keys].every(k=>(a[k]||0)===(b[k]||0));};
let status='ok',message='',answer='',targetLabel='',targetNorm='',targetIndex=-1,sourceAdded=false,cleanupRestored=false;
let before=[],afterAdd=[],selectedBefore=[],selectedAfter=[],selectedRestored=[];
let beforeSubmitScreenshotPath='',afterResponseScreenshotPath='';
try{
  const values=await notebookValues();
  before=await sourceState();
  if(/accounts\.google\.com|ServiceLogin/i.test(initialUrl))throw new Error('NotebookLM 로그인이 필요합니다.');
  if(!initialUrl.includes(payload.notebookId)||!values.includes(payload.notebookTitle))throw new Error('NotebookLM 정본 제목/ID를 확인하지 못했습니다.');
  if(before.length>=50)throw new Error('NotebookLM 소스가 50개라 새 소스를 추가할 자리가 없습니다.');
  const add=p.locator('button[aria-label="출처 추가"]').first();
  if(!(await add.count()))throw new Error('NotebookLM 출처 추가 버튼을 찾지 못했습니다.');
  await add.click();await sleep(700);
  const webClicked=await p.evaluate(()=>{const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const button=[...document.querySelectorAll('button,[role="button"]')].find(e=>visible(e)&&/웹사이트/.test((e.innerText||e.textContent||'').trim()));if(!button)return false;button.click();return true;});
  if(!webClicked)throw new Error('NotebookLM 웹사이트 소스 컨트롤을 찾지 못했습니다.');
  await sleep(500);
  const urlInput=p.locator('textarea[aria-label="URL 입력"],input[aria-label="URL 입력"]').first();
  if(!(await urlInput.count()))throw new Error('NotebookLM URL 입력칸을 찾지 못했습니다.');
  await urlInput.fill(payload.url);
  const inserted=await p.evaluate(()=>{const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const button=[...document.querySelectorAll('button')].find(e=>visible(e)&&(e.innerText||e.textContent||'').trim()==='삽입'&&!e.disabled);if(!button)return false;button.click();return true;});
  if(!inserted)throw new Error('NotebookLM 소스 삽입 버튼이 활성화되지 않았습니다.');
  const beforeCounts=labelCounts(before.map(x=>x.label)),addDeadline=Date.now()+120000;
  while(Date.now()<addDeadline){
    afterAdd=await sourceState();
    if(afterAdd.length===before.length+1){
      const afterCounts=labelCounts(afterAdd.map(x=>x.label));
      const changed=Object.keys(afterCounts).filter(key=>(afterCounts[key]||0)===(beforeCounts[key]||0)+1);
      if(changed.length===1){targetNorm=changed[0];targetIndex=afterAdd.map(x=>norm(x.label)).lastIndexOf(targetNorm);targetLabel=afterAdd[targetIndex]?.label||'';break;}
    }
    await sleep(1000);
  }
  if(!targetLabel||targetIndex<0)throw new Error('NotebookLM에 추가된 소스를 한 개로 특정하지 못했습니다.');
  sourceAdded=true;
  const boxes=p.locator('input[type="checkbox"]:not([aria-label="모든 출처 선택"])');
  let targetBox=null;
  for(let i=0;i<await boxes.count();i++){const label=await boxes.nth(i).getAttribute('aria-label');if(norm(label)===targetNorm)targetBox=boxes.nth(i);}
  if(!targetBox)throw new Error('NotebookLM 새 소스 체크박스를 찾지 못했습니다.');
  const enableDeadline=Date.now()+120000;
  while(Date.now()<enableDeadline&&!(await targetBox.isEnabled()))await sleep(1000);
  if(!(await targetBox.isEnabled()))throw new Error('NotebookLM 새 소스가 준비되지 않았습니다.');
  for(let i=0;i<await boxes.count();i++){const box=boxes.nth(i);if(await box.isChecked())await box.click();}
  if(!(await targetBox.isChecked()))await targetBox.click();
  await sleep(800);
  selectedBefore=(await sourceState()).filter(x=>x.checked).map(x=>x.label);
  if(selectedBefore.length!==1||norm(selectedBefore[0])!==targetNorm)throw new Error('NotebookLM 제출 직전 대상 소스 하나만 선택되지 않았습니다.');
  await p.screenshot({path:payload.evidencePrefix+'-before-submit.png',fullPage:true});
  beforeSubmitScreenshotPath=await fs.resolvePath(payload.evidencePrefix+'-before-submit.png');
  const chatBefore=await p.locator('.chat-message-pair').count();
  const query=p.locator('textarea[aria-label="쿼리 상자"]').first();
  if(!(await query.count()))throw new Error('NotebookLM 쿼리 입력칸을 찾지 못했습니다.');
  await query.fill(payload.prompt);await sleep(400);
  const submitted=await p.evaluate(()=>{const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};const buttons=[...document.querySelectorAll('button')].filter(e=>e.getAttribute('aria-label')==='제출'&&!e.disabled&&visible(e));if(!buttons.length)return false;buttons[buttons.length-1].click();return true;});
  if(!submitted)await query.press('Enter');
  const answerDeadline=Date.now()+240000;
  while(Date.now()<answerDeadline){const state=await p.evaluate(()=>{const pairs=[...document.querySelectorAll('.chat-message-pair')],last=pairs[pairs.length-1];const value=(last?.querySelector('.to-user-container .message-text-content')?.innerText||'').trim();const done=!!last?.querySelector('button[aria-label="클립보드에 모델 대답 복사"]');return {count:pairs.length,value,done};});if(state.count>chatBefore&&state.done&&state.value.length>100){answer=state.value;break;}await sleep(1200);}
  if(!answer)throw new Error('NotebookLM 응답을 제한 시간 안에 확인하지 못했습니다.');
  selectedAfter=(await sourceState()).filter(x=>x.checked).map(x=>x.label);
  if(selectedAfter.length!==1||norm(selectedAfter[0])!==targetNorm)throw new Error('NotebookLM 응답 후 소스 범위가 바뀌었습니다.');
  await p.screenshot({path:payload.evidencePrefix+'-after-response.png',fullPage:true});
  afterResponseScreenshotPath=await fs.resolvePath(payload.evidencePrefix+'-after-response.png');
}catch(error){status='error';message=String(error?.message||error);}
if(sourceAdded){
  try{
    const menus=p.locator('button[aria-label="더보기"]');let menuIndex=-1;
    for(let i=0;i<await menus.count();i++){const desc=await menus.nth(i).getAttribute('aria-description');if(norm(desc)===targetNorm){menuIndex=i;break;}}
    if(menuIndex<0)throw new Error('추가한 NotebookLM 소스의 삭제 메뉴를 찾지 못했습니다.');
    await menus.nth(menuIndex).click();await sleep(500);
    const del=p.locator('[role="menuitem"]').filter({hasText:'소스 삭제'}).last();
    if(!(await del.count()))throw new Error('NotebookLM 소스 삭제 항목을 찾지 못했습니다.');
    await del.click();await sleep(700);
    const dialog=p.locator('[role="dialog"]').last();
    if(await dialog.count()){const confirm=dialog.locator('button').filter({hasText:/^삭제$/}).last();if(await confirm.count()){await confirm.click();await sleep(700);}}
    const cleanupDeadline=Date.now()+30000;
    while(Date.now()<cleanupDeadline){const state=await sourceState();if(state.length===before.length&&!state.some(x=>norm(x.label)===targetNorm))break;await sleep(500);}
    const currentBoxes=p.locator('input[type="checkbox"]:not([aria-label="모든 출처 선택"])');
    for(let i=0;i<await currentBoxes.count();i++){const box=currentBoxes.nth(i);if(await box.isChecked())await box.click();}
    const wanted=labelCounts(before.filter(x=>x.checked).map(x=>x.label));
    const applied={};
    for(let i=0;i<await currentBoxes.count();i++){const box=currentBoxes.nth(i),label=await box.getAttribute('aria-label'),key=norm(label);if((applied[key]||0)<(wanted[key]||0)){await box.click();applied[key]=(applied[key]||0)+1;}}
    selectedRestored=(await sourceState()).filter(x=>x.checked).map(x=>x.label);
    const finalState=await sourceState();
    cleanupRestored=finalState.length===before.length&&!finalState.some(x=>norm(x.label)===targetNorm)&&sameCounts(selectedRestored,before.filter(x=>x.checked).map(x=>x.label));
    if(!cleanupRestored)throw new Error('NotebookLM 소스 수/선택 상태가 원래대로 복구되지 않았습니다.');
  }catch(error){status='error';message=[message,String(error?.message||error)].filter(Boolean).join(' | ');}
}
await p.close();
emit({status,message,backend:'Aside CLI headless REPL',account:'u0',kind:payload.kind,notebookId:payload.notebookId,notebookTitle:payload.notebookTitle,targetLabel,sourceCountBefore:before.length,sourceCountAfterAdd:afterAdd.length,selectedBefore,selectedAfter,selectedRestored,sourceAdded,cleanupRestored,beforeSubmitScreenshotPath,afterResponseScreenshotPath,answer});
'''
    PROVIDER_LOCK.touch(exist_ok=True)
    with PROVIDER_LOCK.open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        result = run_repl(code, timeout=max(360, int(timeout) + 90), account=account)
    if result.get("status") != "ok":
        raise NotebookLMAsideError(str(result.get("message") or json.dumps(result, ensure_ascii=False)))
    if not result.get("cleanupRestored") or not str(result.get("answer") or "").strip():
        raise NotebookLMAsideError("NotebookLM 응답/소스 복구 증거가 완전하지 않습니다.")
    if evidence_dir:
        evidence_root = Path(evidence_dir).expanduser().resolve()
        evidence_root.mkdir(parents=True, exist_ok=True)
        for key, name in (
            ("beforeSubmitScreenshotPath", "notebooklm-before-submit.png"),
            ("afterResponseScreenshotPath", "notebooklm-after-response.png"),
        ):
            source = Path(str(result.get(key) or ""))
            if not source.is_file():
                raise NotebookLMAsideError(f"NotebookLM 증거 스크린샷이 없습니다: {key}")
            target = evidence_root / name
            shutil.copy2(source, target)
            result[key] = str(target)
        safe_evidence = {
            key: value
            for key, value in result.items()
            if key not in {"answer", "message"}
        }
        safe_evidence["prompt"] = prompt
        safe_evidence["sourceUrl"] = youtube_url
        (evidence_root / "notebooklm-answer.md").write_text(
            str(result["answer"]).strip() + "\n",
            encoding="utf-8",
        )
        (evidence_root / "notebooklm-provider-evidence.json").write_text(
            json.dumps(safe_evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result
