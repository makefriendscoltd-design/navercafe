#!/usr/bin/env python3
"""Keep a pinned NotebookLM notebook below its source cap.

Every run adds its video as a source, asks, and removes it again. When that
removal fails the source stays, and at NotebookLM's 50-source cap no further
source can be added -- so no manuscript is written at all. Cafe production spent
five days in exactly that state before anyone looked.

Nothing in a notebook needs to survive a run: the answer is saved into the run
folder, which is what the lineage gates read, and the next run adds whatever it
needs. So trimming is safe, and the only care required is not deleting a source
some other run is using right now, which is why the list is snapshotted once
rather than recomputed between deletions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from aside_browser import JS_COMMON, _payload_expression, ensure_daemon, run_repl
from content_production_policy import CAFE_NOTEBOOK, SHORTS_NOTEBOOK

NOTEBOOKS = {"cafe": CAFE_NOTEBOOK, "shorts": SHORTS_NOTEBOOK}

LIST_JS = r'''
const p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&t=${Date.now()}`);
try{
 await sleep(10000);
 const rows=await p.evaluate(()=>[...document.querySelectorAll('.single-source-container')]
   .map(r=>(r.innerText||'').replace(/\s+/g,' ').replace(/^video_youtube\s*/,'').trim()));
 emit({status:'ok',rows});
}catch(e){emit({status:'error',message:String(e?.message||e)});}
finally{try{await p.close();}catch(_){}}
'''

DELETE_JS = r'''
const p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&t=${Date.now()}`);
try{
 await sleep(10000);
 const before=await p.evaluate(()=>document.querySelectorAll('.single-source-container').length);
 // Match on letters alone: titles carry curly quotes and uneven spacing.
 const opened=await p.evaluate(label=>{
   const key=s=>s.toLowerCase().replace(/[^a-z0-9]/g,'');
   const want=key(label);
   const row=[...document.querySelectorAll('.single-source-container')]
     .find(r=>key((r.innerText||'').replace(/^video_youtube\s*/,'')).startsWith(want));
   if(!row)return 'no_row';
   const more=[...row.querySelectorAll('button')]
     .find(b=>(b.getAttribute('aria-label')||'').includes('더보기'));
   if(!more)return 'no_menu';
   more.click();return 'ok';
 },payload.label);
 if(opened!=='ok'){emit({status:'error',message:opened,before});return;}
 await sleep(1200);
 // The menu renders in an overlay, and its label sits in a span.
 const picked=await p.evaluate(()=>{
   const label=[...document.querySelectorAll('.mat-mdc-menu-item-text,span,div')]
     .find(e=>!e.children.length&&/^(소스 삭제|소스 제거|Delete source|Remove source)$/
       .test((e.innerText||'').trim()));
   if(!label)return false;
   (label.closest('[role=menuitem],button,.mat-mdc-menu-item')||label).click();
   return true;
 });
 if(!picked){emit({status:'error',message:'no_delete_item',before});return;}
 await sleep(1200);
 await p.evaluate(()=>{
   const ok=[...document.querySelectorAll('button')]
     .find(b=>/^(삭제|확인|Delete|Remove)$/.test((b.innerText||'').trim())
              &&b.getBoundingClientRect().width>0);
   if(ok)ok.click();
 });
 await sleep(3500);
 const after=await p.evaluate(()=>document.querySelectorAll('.single-source-container').length);
 emit({status:'ok',before,after});
}catch(e){emit({status:'error',message:String(e?.message||e)});}
finally{try{await p.close();}catch(_){}}
'''


def _run(script: str, payload: dict, *, timeout: int = 300) -> dict:
    return run_repl(JS_COMMON + "\nconst payload=" + _payload_expression(payload) + ";\n" + script,
                    account="u0", timeout=timeout)


def trim(kind: str, *, keep: int, apply: bool = False) -> dict:
    notebook = NOTEBOOKS[kind]
    payload = {"notebookId": notebook["id"]}
    rows = _run(LIST_JS, payload)["rows"]
    # Snapshot once. Recomputing "the extras" between deletions would also take
    # sources a concurrent run added while this was working.
    doomed = rows[:max(0, len(rows) - keep)]
    result = {"notebook": kind, "title": notebook["title"], "sources": len(rows),
              "keep": keep, "to_delete": len(doomed), "deleted": 0, "skipped": []}
    if not apply or not doomed:
        return result
    for label in doomed:
        outcome = _run(DELETE_JS, {**payload, "label": label[:40]})
        if outcome.get("status") != "ok" or outcome.get("after") != outcome.get("before", 0) - 1:
            result["skipped"].append(label[:48])
            continue
        result["deleted"] += 1
    result["sources_after"] = _run(LIST_JS, payload)["rows"].__len__()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook", choices=sorted(NOTEBOOKS) + ["all"], default="all")
    parser.add_argument("--keep", type=int, default=10, help="남길 소스 수")
    parser.add_argument("--apply", action="store_true", help="실제로 삭제한다")
    args = parser.parse_args(argv)
    if args.keep < 0:
        parser.error("--keep은 0 이상이어야 합니다")
    if not ensure_daemon():
        print(json.dumps({"status": "error", "reason": "aside_daemon_down"}, ensure_ascii=False))
        return 1
    kinds = sorted(NOTEBOOKS) if args.notebook == "all" else [args.notebook]
    for kind in kinds:
        print(json.dumps(trim(kind, keep=args.keep, apply=args.apply), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
