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
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import (
    ASIDE_ACCOUNT,
    CAFE_NOTEBOOK,
    SHORTS_NOTEBOOK,
    provider_answer_content_signature,
)

NOTEBOOKS = {"shorts": SHORTS_NOTEBOOK, "cafe": CAFE_NOTEBOOK}


PROJECT = Path(__file__).resolve().parent

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
  if(!matched.length)return {matchCount:0,pairCount:pairs.length};
  const read=({index,node})=>{
    const blocks=[...node.querySelectorAll(BLOCK_SELECTOR)];
    return {pairIndex:index,blockCount:blocks.length,
      citationCount:node.querySelectorAll(CITATION_SELECTOR).length,
      indexedSpanCount:node.querySelectorAll('span[data-start-index]').length,
      answer:blocks.map(ownText).filter(Boolean).join('\n')};
  };
  return {matchCount:matched.length,pairCount:pairs.length,
    candidates:matched.map(read)};
},payload.anchors);
if(!out.matchCount){emit({status:'error',message:`앵커로 원응답을 찾지 못했습니다(pairs=${out.pairCount}).`});return;}
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


def newest_attempt(project: Path, kind: str = "shorts") -> tuple[str, str]:
    """Which source made the most recent attempt in the pinned notebook."""
    newest = ("", "")
    for ledger in project.glob(f"outputs/*/{kind}/notebooklm-attempt-ledger.json"):
        try:
            payload = json.loads(ledger.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for attempt in payload.get("attempts") or []:
            at = str(attempt.get("updated_at") or "")
            if at > newest[0]:
                newest = (at, str(payload.get("sourceKey") or ""))
    return newest


def adopt_latest_answer(
    source_key: str,
    *,
    out_dir: str | Path,
    kind: str = "shorts",
    project: Path | None = None,
    account: str = ASIDE_ACCOUNT,
    timeout: int = 240,
) -> dict:
    """Adopt the notebook's newest answer for an attempt that died before saving.

    An attempt can die after the provider answered but before the answer was
    written, leaving it reachable only in the DOM: recovery cannot anchor on a
    stored copy and the absence probe correctly refuses to call it absent, so the
    source can never be produced. Adoption is safe exactly when no other source
    could own that answer -- when this source made the most recent attempt in the
    notebook. It re-reads what is already there and calls no provider.
    """
    project = Path(project or PROJECT).resolve()
    at, owner = newest_attempt(project, kind)
    if owner != source_key or not at:
        raise RuntimeError(
            f"최신 시도가 이 원본의 것이 아니어서 노트북 최신 답변을 채택할 수 없습니다({owner or '없음'}).")
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    notebook = NOTEBOOKS[kind]
    payload = {"notebookId": notebook["id"], "anchors": [],
               "screenshotName": f"notebooklm-{source_key}-adopted-answer.png"}
    result = run_repl(JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + RECOVER_JS,
                      timeout=timeout, account=account)
    candidates = [dict(item) for item in (result.get("candidates") or [])]
    if not candidates:
        raise RuntimeError("공급자 DOM에서 원응답 본문을 읽지 못했습니다.")
    selected = max(candidates, key=lambda item: int(item.get("pairIndex") or 0))
    answer = str(selected["answer"]).strip()
    if not answer:
        raise RuntimeError("노트북 최신 답변이 비어 있습니다.")
    answer_path = out / "notebooklm-answer-recovered.md"
    answer_path.write_text(answer + "\n", encoding="utf-8")
    evidence = {
        "schema": "notebooklm-answer-recovery/v1",
        "source_key": source_key, "notebook_id": notebook["id"],
        "notebook_title": notebook["title"], "account": account,
        "backend": "Aside CLI headless REPL",
        "mode": "paragraph_blocks_without_citations",
        "provenance": "adopted_latest_after_interrupted_attempt",
        "provider_call_made": False, "source_added": False, "prompt_submitted": False,
        "newest_attempt_at": at, "newest_attempt_source": owner,
        "url": result.get("url"), "pair_index": selected.get("pairIndex"),
        "pair_count": result.get("pairCount"),
        "block_count": selected.get("blockCount"),
        "citation_count": selected.get("citationCount"),
        "indexed_span_count": selected.get("indexedSpanCount"),
        "screenshot_path": result.get("screenshotPath"),
        "recovered_answer_path": str(answer_path),
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = out / "notebooklm-answer-recovery-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"answer_path": str(answer_path), "evidence_path": str(evidence_path), **evidence}


def recover_answer(
    source_key: str,
    stored_answer_path: str | Path,
    *,
    out_dir: str | Path,
    kind: str = "shorts",
    expect_sha256: str | None = None,
    account: str = ASIDE_ACCOUNT,
    timeout: int = 240,
) -> dict:
    """Re-read the stored answer from the live DOM and write it with its evidence."""
    stored_path = Path(stored_answer_path).expanduser().resolve()
    stored_answer = stored_path.read_text(encoding="utf-8")
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    anchors = _anchors(stored_answer)
    notebook = NOTEBOOKS[kind]
    payload = {
        "notebookId": notebook["id"],
        "anchors": anchors,
        "screenshotName": f"notebooklm-{source_key}-recovered-answer.png",
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + RECOVER_JS
    result = run_repl(code, timeout=timeout, account=account)
    candidates = [dict(item) for item in (result.get("candidates") or [])]
    if not candidates:
        raise RuntimeError("공급자 DOM에서 원응답 본문을 읽지 못했습니다.")
    if expect_sha256:
        # The same source can appear more than once in the chat. The provider
        # evidence already hashes the exact answer file, so let that decide which
        # response is the one this run recorded.
        chosen = [c for c in candidates
                  if hashlib.sha256((str(c["answer"]).strip() + "\n").encode("utf-8")).hexdigest()
                  == expect_sha256]
        if len(chosen) != 1:
            raise RuntimeError(
                f"공급자 증거 해시와 일치하는 응답이 정확히 1개가 아닙니다 ({len(chosen)}개)."
            )
        selected = chosen[0]
    else:
        if len(candidates) != 1:
            raise RuntimeError(
                f"앵커로 원응답을 유일하게 찾지 못했습니다 ({len(candidates)}개). "
                "--expect-sha256으로 특정하세요."
            )
        selected = candidates[0]
        stored_signature = provider_answer_content_signature(stored_answer)
        if provider_answer_content_signature(str(selected["answer"])) != stored_signature:
            raise RuntimeError(
                "재조회한 본문이 저장된 원응답과 글자 단위로 다릅니다. 저장하지 않고 중단합니다."
            )
    answer = str(selected["answer"]).strip()
    result = {**result, **selected}
    answer_path = out / "notebooklm-answer-recovered.md"
    answer_path.write_text(answer + "\n", encoding="utf-8")
    evidence = {
        "schema": "notebooklm-answer-recovery/v1",
        "source_key": source_key,
        "notebook_id": notebook["id"],
        "notebook_title": notebook["title"],
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
    parser.add_argument("--stored-answer",
                        help="대조할 기존 저장 원응답. --adopt-latest 를 쓰면 생략한다")
    parser.add_argument("--adopt-latest", action="store_true",
                        help="저장본 없이 죽은 시도의 답변을 채택한다(최신 시도가 이 원본일 때만)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--kind", choices=sorted(NOTEBOOKS), default="shorts")
    parser.add_argument("--expect-sha256",
                        help="공급자 증거의 answer_sha256. 같은 소스 응답이 여러 개일 때 특정한다")
    args = parser.parse_args(argv)
    if args.adopt_latest:
        result = adopt_latest_answer(args.source_key, out_dir=args.out_dir, kind=args.kind)
        print(f"최신 답변 채택: {result['answer_path']}")
        print(f"증거: {result['evidence_path']}")
        return 0
    if not args.stored_answer:
        parser.error("--stored-answer 또는 --adopt-latest 중 하나가 필요합니다")

    result = recover_answer(args.source_key, args.stored_answer,
                            out_dir=args.out_dir, kind=args.kind,
                            expect_sha256=args.expect_sha256)
    print(f"원응답 재조회 완료: {result['recovered_answer_path']}")
    print(
        "citation {citation_count}개 / indexed span {indexed_span_count}개 / "
        "블록 {block_count}개".format(**result)
    )
    print(f"증거: {result['evidence_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
