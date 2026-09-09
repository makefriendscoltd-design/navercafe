"""Delete one Naver Cafe article this project published from a wrong source.

Three columns were written from vertical YouTube Shorts and posted before
anything measured the source. There is no longform original to rewrite them
from, so they come down.

Deleting is irreversible, so the article is identified twice before the button
is touched: the page has to carry the manifest's exact title and its source key.
Anything else and the run stops without clicking.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from aside_browser import JS_COMMON, _payload_expression, run_repl

INSPECT = r'''
let p=null;
try{
 p=await openTab(payload.url+(payload.url.includes('?')?'&':'?')+'remove_probe='+Date.now());
 await sleep(4500);
 let text='',html='';
 for(const ctx of await contextsFor(p)){
  try{const part=await ctx.evaluate(()=>({t:document.body?.innerText||'',h:document.body?.innerHTML||''}));
      text+='\n'+part.t;html+='\n'+part.h;}catch(_){}
 }
 emit({status:'ok',url:p.url(),
       titleExact:text.includes(payload.title),
       sourceKey:text.includes(payload.sourceKey)||html.includes(payload.sourceKey),
       loginWall:/nid\.naver\.com/.test(p.url()),
       excerpt:text.replace(/\s+/g,' ').slice(0,400)});
}catch(e){emit({status:'error',message:String(e?.message||e)});}
finally{try{if(p)await p.close();}catch(_){}}
'''

REMOVE = r'''
// Naver renders the article in its own frame. The page chrome also has a link
// reading 삭제 -- it clears read notifications -- so the article frame has to be
// picked by URL before anything is clicked.
const SEL='a.BaseButton:has-text("삭제")';
let p=null;
try{
 p=await openTab(payload.url+(payload.url.includes('?')?'&':'?')+'remove_run='+Date.now());
 await sleep(4500);
 let target=null;
 for(const ctx of await contextsFor(p)){
  let where='';
  try{where=await ctx.evaluate(()=>location.href);}catch(_){continue;}
  if(!where.includes('/articles/'+payload.articleId)) continue;
  if(await ctx.locator(SEL).count().catch(()=>0)>0){target=ctx;break;}
 }
 if(!target){emit({status:'no_delete_control',url:p.url()});}
 else{
  await target.locator(SEL).first().click();
  await sleep(2000);
  // The confirmation is an in-page layer, not a native dialog.
  let confirmed=false;
  for(const ctx of await contextsFor(p)){
   const ok=ctx.locator('.button_confirm, a:has-text("확인"), button:has-text("확인")');
   if(await ok.count().catch(()=>0)>0){await ok.first().click();confirmed=true;break;}
  }
  await sleep(4000);
  const after=await openTab(payload.url+(payload.url.includes('?')?'&':'?')+'remove_verify='+Date.now());
  await sleep(4000);
  let text='';
  for(const ctx of await contextsFor(after)){
   try{text+='\n'+(await ctx.evaluate(()=>document.body?.innerText||''));}catch(_){}
  }
  await after.close();
  emit({status:'clicked',confirmed,
        gone:/삭제된 게시글|삭제되었|존재하지 않는 게시글|없는 게시글/.test(text),
        stillHasTitle:text.includes(payload.title),
        excerpt:text.replace(/\s+/g,' ').slice(0,300)});
 }
}catch(e){emit({status:'error',message:String(e?.message||e)});}
finally{try{if(p)await p.close();}catch(_){}}
'''


def run(script: str, payload: dict) -> dict:
    return run_repl(JS_COMMON + "\nconst payload=" + _payload_expression(payload) + ";\n" + script,
                    account="u0", timeout=240)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--article-url", required=True)
    parser.add_argument("--apply", action="store_true", help="검증만 하지 않고 실제로 삭제한다")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    article_id = re.search(r"(?:/|articleid(?:%3D|=))(\d{2,})", args.article_url, re.I)
    if not article_id:
        raise SystemExit("게시글 번호를 URL에서 찾지 못했습니다.")
    payload = {"url": args.article_url, "title": manifest["title"],
               "sourceKey": manifest["source_key"], "articleId": article_id[1]}
    found = run(INSPECT, payload)
    if found.get("status") != "ok" or found.get("loginWall"):
        print(json.dumps({"status": "cannot_inspect", **found}, ensure_ascii=False))
        return 1
    if not (found.get("titleExact") and found.get("sourceKey")):
        print(json.dumps({"status": "identity_mismatch", **found}, ensure_ascii=False))
        return 1
    if not args.apply:
        print(json.dumps({"status": "identified", **payload, **found}, ensure_ascii=False))
        return 0

    result = run(REMOVE, payload)
    removed = bool(result.get("gone")) or (
        result.get("status") == "clicked" and not result.get("stillHasTitle"))
    print(json.dumps({**result, "status": "removed" if removed else "not_removed",
                      "clickOutcome": result.get("status"),
                      "sourceKey": manifest["source_key"], "url": args.article_url},
                     ensure_ascii=False))
    return 0 if removed else 1


if __name__ == "__main__":
    raise SystemExit(main())
