"""Synchronize the pinned NotebookLM instruction with one save and a fresh re-read."""
from __future__ import annotations
import argparse
import ast
import fcntl
import json
from pathlib import Path
import subprocess
from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import ASIDE_ACCOUNT, SHORTS_NOTEBOOK, SHORTS_NOTEBOOK_INSTRUCTION, notebook_instruction_sha256
EXPECTED_NOTEBOOK_ID = SHORTS_NOTEBOOK['id']
EXPECTED_NOTEBOOK_TITLE = SHORTS_NOTEBOOK['title']
EXPECTED_ACCOUNT = ASIDE_ACCOUNT
EXPECTED_BACKEND = 'Aside CLI headless REPL'
PROJECT = Path(__file__).resolve().parent

def build_provider_code(old_instruction: str) -> str:
    previous = old_instruction
    payload = {
        "notebookId": EXPECTED_NOTEBOOK_ID,
        "notebookTitle": EXPECTED_NOTEBOOK_TITLE,
        "account": EXPECTED_ACCOUNT,
        "backend": EXPECTED_BACKEND,
        "oldInstruction": previous,
        "newInstruction": SHORTS_NOTEBOOK_INSTRUCTION,
    }
    return JS_COMMON + "\n{\nconst payload=" + _payload_expression(payload) + ";\n" + r'''
const normalizeInstruction=value=>(value||'').replace(/\r\n?/g,'\n').normalize('NFKC').trim();
const exactVisibleAttached=async(locator,label)=>{const count=await locator.count();if(count!==1)throw new Error(`${label} cardinality ${count}`);const item=locator.first();if(!(await item.isVisible()))throw new Error(`${label} is not visible`);let attached=false;try{attached=await item.evaluate(el=>el.isConnected);}catch(_){attached=false;}if(!attached)throw new Error(`${label} is not attached`);return item;};
const state=async p=>await p.evaluate(({notebookId,notebookTitle})=>{const inputs=[...document.querySelectorAll('input')].map(x=>(x.value||'').trim()).filter(Boolean);const controls=[...document.querySelectorAll('button,[role="button"]')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';});return {host:location.host,path:location.pathname,loginRequired:/accounts\.google\.com|ServiceLogin/i.test(location.href),documentTitleExact:document.title===notebookTitle||document.title===`${notebookTitle} - Gemini Notebook`,notebookTitleExactMatches:inputs.filter(x=>x===notebookTitle).length,accountMenuPresent:controls.some(x=>(x.getAttribute('aria-label')||'').startsWith('Google 계정:')),sourceCount:document.querySelectorAll('input[type="checkbox"]:not([aria-label="모든 출처 선택"])').length,chatPairCount:document.querySelectorAll('.chat-message-pair').length,chatMessageCount:document.querySelectorAll('.chat-message-pair .message-text-content').length};},{notebookId:payload.notebookId,notebookTitle:payload.notebookTitle});
const readPanel=async dialog=>await dialog.evaluate(dialog=>{const textarea=dialog.querySelector('textarea[aria-label="채팅 응답을 제어하는 맞춤 프롬프트"]');const value=textarea?.value||'';const radios=[...dialog.querySelectorAll('[role="radio"]')].map(e=>({text:(e.innerText||e.textContent||'').trim(),selected:e.getAttribute('aria-checked')==='true'}));const selected=radios.filter(x=>x.selected).map(x=>x.text);const counter=((dialog.innerText||dialog.textContent||'').match(/(\d+)\s*\/\s*(\d+)/)||[]);return {found:!!textarea,value,utf16Length:value.length,counterCurrent:counter[1]?Number(counter[1]):null,goal:selected.find(x=>['기본값','학습 가이드','맞춤'].includes(x))||'',responseLength:[...selected].reverse().find(x=>['기본값','길게','짧게'].includes(x))||''};});
const assertBinding=(snapshot,label)=>{if(snapshot.loginRequired)throw new Error(`NotebookLM login required ${label}`);if(!['notebook.google.com','notebooklm.google.com'].includes(snapshot.host)||snapshot.path!==`/notebook/${payload.notebookId}`)throw new Error(`NotebookLM exact route mismatch ${label}`);if(!snapshot.documentTitleExact||snapshot.notebookTitleExactMatches!==1||!snapshot.accountMenuPresent)throw new Error(`NotebookLM exact title/account binding failed ${label}`);};
const panelSummary=panel=>({found:panel.found,utf16Length:panel.utf16Length,counterCurrent:panel.counterCurrent,goal:panel.goal,responseLength:panel.responseLength});
const steps={beforeStateRead:false,configOpened:false,beforeInstructionMatched:false,textFilled:false,saveClickAttempted:false,saveClicked:false,requeryOpened:false,afterInstructionMatched:false};
let p=null,saveClickCount=0,before=null,after=null,panelBefore=null,panelAfter=null,beforeValue='',afterValue='';
try{const oldNormalized=normalizeInstruction(payload.oldInstruction),newNormalized=normalizeInstruction(payload.newInstruction);p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&aside_pipeline=v16_instruction_migration`);await sleep(5000);before=await state(p);steps.beforeStateRead=true;assertBinding(before,'before');const configButton=await exactVisibleAttached(p.locator('button[aria-label="노트북 구성"]'),'NotebookLM config button before');await configButton.click();await sleep(800);steps.configOpened=true;const dialog=await exactVisibleAttached(p.locator('[role="dialog"]').filter({hasText:'채팅 설정'}),'NotebookLM chat settings dialog before');const textarea=await exactVisibleAttached(dialog.locator('textarea[aria-label="채팅 응답을 제어하는 맞춤 프롬프트"]'),'NotebookLM instruction textarea before');panelBefore=await readPanel(dialog);beforeValue=panelBefore.value||'';if(!panelBefore.found||panelBefore.counterCurrent!==panelBefore.utf16Length)throw new Error('NotebookLM instruction panel unreadable before');if(panelBefore.goal!=='맞춤'||panelBefore.responseLength!=='길게')throw new Error('NotebookLM setting is not custom/long before');const beforeNormalized=normalizeInstruction(beforeValue);if(beforeNormalized!==oldNormalized&&beforeNormalized!==newNormalized)throw new Error('live instruction is neither sealed v15 nor policy v16');steps.beforeInstructionMatched=true;if(beforeNormalized===oldNormalized){await textarea.fill(payload.newInstruction);await sleep(500);if(normalizeInstruction(await textarea.inputValue())!==newNormalized)throw new Error('NotebookLM v16 fill mismatch');steps.textFilled=true;const saveButton=await exactVisibleAttached(dialog.locator('button').filter({hasText:/^저장$/}),'NotebookLM save button');if(!(await saveButton.isEnabled()))throw new Error('NotebookLM save button unavailable');saveClickCount+=1;if(saveClickCount>1)throw new Error('save click budget exceeded');steps.saveClickAttempted=true;await saveButton.click();steps.saveClicked=true;await sleep(1200);}await p.close();p=null;p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&aside_pipeline=v16_instruction_requery_${Date.now()}`);steps.requeryOpened=true;await sleep(5000);after=await state(p);assertBinding(after,'after');const freshConfigButton=await exactVisibleAttached(p.locator('button[aria-label="노트북 구성"]'),'NotebookLM config button after');await freshConfigButton.click();await sleep(800);const freshDialog=await exactVisibleAttached(p.locator('[role="dialog"]').filter({hasText:'채팅 설정'}),'NotebookLM chat settings dialog after');await exactVisibleAttached(freshDialog.locator('textarea[aria-label="채팅 응답을 제어하는 맞춤 프롬프트"]'),'NotebookLM instruction textarea after');panelAfter=await readPanel(freshDialog);afterValue=panelAfter.value||'';if(!panelAfter.found||panelAfter.counterCurrent!==panelAfter.utf16Length)throw new Error('NotebookLM instruction panel unreadable after');if(panelAfter.goal!=='맞춤'||panelAfter.responseLength!=='길게')throw new Error('NotebookLM setting is not custom/long after');if(normalizeInstruction(afterValue)!==newNormalized)throw new Error('NotebookLM v16 requery mismatch');steps.afterInstructionMatched=true;if(before.sourceCount!==after.sourceCount||before.chatPairCount!==after.chatPairCount||before.chatMessageCount!==after.chatMessageCount)throw new Error('NotebookLM source/chat counts changed');emit({status:'ok',backend:payload.backend,account:payload.account,headless:true,saveClickCount,before,after,panelBefore:panelSummary(panelBefore),panelAfter:panelSummary(panelAfter),steps,beforeValue,afterValue});}catch(error){emit({status:'failed',message:String(error?.message||error),backend:payload.backend,account:payload.account,headless:true,saveClickCount,before,after,panelBefore:panelBefore?panelSummary(panelBefore):null,panelAfter:panelAfter?panelSummary(panelAfter):null,steps,beforeValue,afterValue});}finally{try{if(p)await p.close();}catch(_){}}
}
'''

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-commit', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    previous = subprocess.run(['git', 'show', args.previous_commit + ':content_production_policy.py'], cwd=PROJECT, capture_output=True, text=True, check=True).stdout
    tree = ast.parse(previous)
    old = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'SHORTS_NOTEBOOK_INSTRUCTION' for target in node.targets))
    code = build_provider_code(old)
    # Parse the exact provider code before touching the shared notebook.
    syntax = subprocess.run(['node', '-e', 'new Function("return (async()=>{"+require("fs").readFileSync(0,"utf8")+"})()");'], input=code, text=True, capture_output=True)
    if syntax.returncode:
        raise RuntimeError('provider JavaScript syntax failed')
    if not args.apply:
        print(json.dumps({'status':'pass','scope':'local_preflight','previous_sha256':notebook_instruction_sha256(old),'next_sha256':notebook_instruction_sha256(SHORTS_NOTEBOOK_INSTRUCTION)}))
        return 0
    subprocess.run(['git','diff','--exit-code','HEAD','--','content_production_policy.py','notebooklm_instruction_sync.py'], cwd=PROJECT, capture_output=True, check=True)
    attempt = args.out.with_suffix('.attempt.json')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with Path('/tmp/aimax-aside-u0-provider.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with attempt.open('x') as stream:
            json.dump({'status':'started','previous_sha256':notebook_instruction_sha256(old),'next_sha256':notebook_instruction_sha256(SHORTS_NOTEBOOK_INSTRUCTION)},stream)
        result = run_repl(code,cwd=PROJECT,timeout=120,account=ASIDE_ACCOUNT)
        before = result.pop('beforeValue','')
        after = result.pop('afterValue','')
        passed = result.get('status')=='ok' and notebook_instruction_sha256(after)==notebook_instruction_sha256(SHORTS_NOTEBOOK_INSTRUCTION) and result.get('saveClickCount') in (0,1)
        result.update({'status':'pass' if passed else 'blocked','before_sha256':notebook_instruction_sha256(before),'after_sha256':notebook_instruction_sha256(after),'source_added':False,'prompt_submitted':False})
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({'status':result['status'],'save_clicks':result.get('saveClickCount'),'evidence':str(args.out)}))
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
