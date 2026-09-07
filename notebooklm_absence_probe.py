"""Prove, by reading the notebook, that a failed attempt produced no answer.

An attempt that dies after the provider call starts blocks every retry, because
nobody can tell from the outside whether an answer was produced -- and retrying
blindly would let a run re-roll answers until one passes. This tool settles the
question the same way the publisher settles an uncertain save: by reading the
provider. When the newest answer in the notebook is provably another source's
already-stored answer, the failed attempt added nothing, and the ledger records
that with its evidence so the pin can let exactly that attempt through.
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
from notebooklm_shorts import (
    ATTEMPT_LEDGER_SCHEMA,
    _default_attempt_ledger_path,
    _read_json_object,
    _write_attempt_ledger,
)


PROBE_JS = r'''
const p=await openTab(`https://notebooklm.google.com/notebook/${payload.notebookId}?authuser=1&absence_probe=${Date.now()}`);
await sleep(6000);
const url=p.url();
if(/accounts\.google\.com|ServiceLogin/i.test(url)){emit({status:'error',message:'NotebookLM 로그인이 필요합니다. '+url});return;}
if(!url.includes(payload.notebookId)){emit({status:'error',message:'정본 NotebookLM 노트북을 열지 못했습니다. '+url});return;}
const out=await p.evaluate(()=>{
  const pairs=[...document.querySelectorAll('.chat-message-pair')];
  const last=pairs[pairs.length-1];
  const node=last?.querySelector('.to-user-container .message-text-content');
  return {pairCount:pairs.length,latest:(node?.innerText||'')};
});
emit({status:'ok',url,...out});
'''


def probe(
    source_key: str,
    *,
    latest_answer_path: str | Path,
    latest_answer_source_key: str,
    evidence_dir: str | Path,
    account: str = ASIDE_ACCOUNT,
) -> dict:
    """Record that no answer exists for ``source_key`` in the pinned notebook."""
    if latest_answer_source_key == source_key:
        raise RuntimeError("부재 증명에는 다른 원본의 최신 답변이 필요합니다.")
    expected = Path(latest_answer_path).expanduser().resolve().read_text(encoding="utf-8")
    payload = {"notebookId": SHORTS_NOTEBOOK["id"]}
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + PROBE_JS
    result = run_repl(code, timeout=180, account=account)
    latest = str(result.get("latest") or "")
    if not latest:
        raise RuntimeError("노트북의 최신 답변을 읽지 못했습니다.")
    if provider_answer_content_signature(latest) != provider_answer_content_signature(expected):
        raise RuntimeError(
            "노트북의 최신 답변이 기대한 이전 원본의 답변과 다릅니다. "
            "이 시도가 답변을 남겼을 수 있으므로 부재로 기록하지 않습니다."
        )
    evidence = {
        "schema": "notebooklm-response-absence/v1",
        "source_key": source_key,
        "notebook_id": SHORTS_NOTEBOOK["id"],
        "account": account,
        "pair_count": int(result.get("pairCount") or 0),
        "latest_answer_belongs_to": latest_answer_source_key,
        "latest_answer_path": str(Path(latest_answer_path).expanduser().resolve()),
        "url": result.get("url"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    out_dir = Path(evidence_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "notebooklm-response-absence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    ledger_path, _ = _default_attempt_ledger_path(source_key, out_dir)
    ledger = _read_json_object(ledger_path)
    if ledger.get("schemaVersion") != ATTEMPT_LEDGER_SCHEMA or ledger.get("sourceKey") != source_key:
        raise RuntimeError("쇼츠 NotebookLM 시도 ledger의 schema/source_key가 다릅니다.")
    attempts = list(ledger.get("attempts") or [])
    cleared = 0
    for attempt in attempts:
        if str(attempt.get("attempt_status") or "") == "unknown_after_provider_start":
            attempt["provider_response_absent"] = True
            attempt["absence_evidence"] = evidence
            cleared += 1
    if not cleared:
        raise RuntimeError("부재로 정리할 unknown_after_provider_start 시도가 없습니다.")
    _write_attempt_ledger(ledger_path, source_key, attempts)
    return {"cleared_attempts": cleared, "ledger": str(ledger_path), **evidence}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--latest-answer", required=True,
                        help="직전에 완료된 다른 원본의 저장된 원응답 파일")
    parser.add_argument("--latest-answer-source", required=True)
    args = parser.parse_args(argv)
    result = probe(
        args.source_key,
        latest_answer_path=args.latest_answer,
        latest_answer_source_key=args.latest_answer_source,
        evidence_dir=args.evidence_dir,
    )
    print(f"응답 부재 확인: 채팅 {result['pair_count']}쌍, 최신 답변은 {result['latest_answer_belongs_to']}")
    print(f"정리한 시도 {result['cleared_attempts']}건 / ledger {result['ledger']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
