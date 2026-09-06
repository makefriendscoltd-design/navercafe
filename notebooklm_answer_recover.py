"""Re-read one NotebookLM answer the provider already returned.

NotebookLM renders every source citation as an inline ``.citation-marker`` chip
inside the answer paragraph, so ``innerText`` interleaves the chip glyph as its
own line and splits one logical paragraph across several lines.  A capture taken
that way fails the v18 six-paragraph gate even though the answer itself is fine.

The retry pin refuses a second extraction for the same source and instruction
hash once an attempt failed substantively, which is deliberate — the answer must
not be re-rolled until one passes.  This tool is the other half of that rule: it
re-reads the same stored response out of the live DOM, keeping the provider
wording verbatim and dropping only the citation chrome.  It makes no new
provider call, adds no source and submits no prompt.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import (
    ASIDE_ACCOUNT,
    SHORTS_NOTEBOOK,
    provider_answer_content_signature,
)


RECOVER_JS = r'''
const p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&answer_recover=${Date.now()}`);
await sleep(6000);
const url=p.url();
if(/accounts\.google\.com|ServiceLogin/i.test(url)){emit({status:'error',message:'NotebookLM 로그인이 필요합니다. '+url});return;}
if(!url.includes(payload.notebookId)){emit({status:'error',message:'정본 NotebookLM 노트북을 열지 못했습니다. '+url});return;}
const out=await p.evaluate(anchors=>{
  const CITATION_SELECTOR='.citation-marker';
  const BLOCK_SELECTOR='div.paragraph,li.paragraph';
  // Take only the text this block owns: skip citation chips entirely, and skip
  // text that belongs to a nested block so a list parent is not duplicated.
  const ownText=el=>{
    let text='';
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,{acceptNode(n){
      const parent=n.parentElement;
      if(!parent)return NodeFilter.FILTER_REJECT;
      if(parent.closest(CITATION_SELECTOR))return NodeFilter.FILTER_REJECT;
      const owner=parent.closest(BLOCK_SELECTOR);
      if(owner&&owner!==el)return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }});
    let n;while((n=walker.nextNode()))text+=n.nodeValue;
    return text.replace(/\s+/g,' ').trim();
  };
  const pairs=[...document.querySelectorAll('.chat-message-pair')];
  const matched=[];
  pairs.forEach((pair,index)=>{
    const node=pair.querySelector('.to-user-container .message-text-content');
    if(node&&anchors.every(anchor=>(node.innerText||'').includes(anchor)))matched.push({index,node});
  });
  if(matched.length!==1)return {matchCount:matched.length,pairCount:pairs.length};
  const {index,node}=matched[0];
  const blocks=[...node.querySelectorAll(BLOCK_SELECTOR)];
  const lines=blocks.map(ownText).filter(Boolean);
  return {matchCount:1,pairIndex:index,pairCount:pairs.length,
    blockCount:blocks.length,
    citationCount:node.querySelectorAll(CITATION_SELECTOR).length,
    indexedSpanCount:node.querySelectorAll('span[data-start-index]').length,
    answer:lines.join('\n'),innerText:node.innerText||''};
},payload.anchors);
if(out.matchCount!==1){emit({status:'error',message:`앵커로 원응답을 유일하게 찾지 못했습니다(matched=${out.matchCount}, pairs=${out.pairCount}).`});return;}
await p.screenshot({path:payload.screenshotName,fullPage:true});
const screenshotPath=await fs.resolvePath(payload.screenshotName);
emit({status:'ok',url,screenshotPath,...out});
'''


def _anchors(stored_answer: str, *, count: int = 6, width: int = 60) -> list[str]:
    """Pick long, distinctive lines that identify one stored answer uniquely."""
    lines = [
        line.strip()
        for line in stored_answer.replace("\r\n", "\n").splitlines()
        if len(line.strip()) >= width
    ]
    if len(lines) < count:
        raise RuntimeError("저장된 원응답에서 고유 앵커로 쓸 긴 줄이 부족합니다.")
    step = max(1, len(lines) // count)
    picked = [lines[index * step][:width] for index in range(count)]
    if len(set(picked)) != count:
        picked = [line[:width] for line in lines[-count:]]
    return picked


def recover_answer(
    source_key: str,
    stored_answer_path: str | Path,
    *,
    out_dir: str | Path,
    account: str = ASIDE_ACCOUNT,
    timeout: int = 240,
) -> dict:
    """Re-read the stored answer from the live DOM and write it with its evidence."""
    stored_path = Path(stored_answer_path).expanduser().resolve()
    stored_answer = stored_path.read_text(encoding="utf-8")
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    anchors = _anchors(stored_answer)
    payload = {
        "notebookId": SHORTS_NOTEBOOK["id"],
        "anchors": anchors,
        "screenshotName": f"notebooklm-{source_key}-recovered-answer.png",
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + RECOVER_JS
    result = run_repl(code, timeout=timeout, account=account)
    answer = str(result.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("공급자 DOM에서 원응답 본문을 읽지 못했습니다.")
    stored_signature = provider_answer_content_signature(stored_answer)
    if provider_answer_content_signature(answer) != stored_signature:
        raise RuntimeError(
            "재조회한 본문이 저장된 원응답과 글자 단위로 다릅니다. 저장하지 않고 중단합니다."
        )
    answer_path = out / "notebooklm-answer-recovered.md"
    answer_path.write_text(answer + "\n", encoding="utf-8")
    evidence = {
        "schema": "notebooklm-answer-recovery/v1",
        "source_key": source_key,
        "notebook_id": SHORTS_NOTEBOOK["id"],
        "notebook_title": SHORTS_NOTEBOOK["title"],
        "account": account,
        "backend": "Aside CLI headless REPL",
        "mode": "paragraph_blocks_without_citations",
        "provider_call_made": False,
        "source_added": False,
        "prompt_submitted": False,
        "url": result.get("url"),
        "pair_index": result.get("pairIndex"),
        "pair_count": result.get("pairCount"),
        "block_count": result.get("blockCount"),
        "citation_count": result.get("citationCount"),
        "indexed_span_count": result.get("indexedSpanCount"),
        "anchors": anchors,
        "screenshot_path": result.get("screenshotPath"),
        "stored_answer_path": str(stored_path),
        "recovered_answer_path": str(answer_path),
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = out / "notebooklm-answer-recovery-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"answer_path": str(answer_path), "evidence_path": str(evidence_path), **evidence}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--stored-answer", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    result = recover_answer(args.source_key, args.stored_answer, out_dir=args.out_dir)
    print(f"원응답 재조회 완료: {result['recovered_answer_path']}")
    print(
        "citation {citation_count}개 / indexed span {indexed_span_count}개 / "
        "블록 {block_count}개".format(**result)
    )
    print(f"증거: {result['evidence_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
