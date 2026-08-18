"""민수대표님_숏폼 노트북에서 쇼츠 나레이션 대본을 받아온다.

포맷은 노트북에 심어져 있다. 여기서 형식을 지시하면 그 설계를 덮어써서
카페 칼럼 같은 평서문 나열이 나온다. 그래서 프롬프트는 최소한으로만 주고,
돌아온 답에서 나레이션 파트만 떼어 쓴다.

노트북 응답 구조:
    ### 영상 분석 결과   <- 수치·화자 배경 등 분석. 나레이션에는 안 들어간다
    ### 선택한 포맷       <- 포맷 [A]/[B]/... 중 노트북이 고른 것
    ### 스크립트          <- 이 아래가 실제 나레이션
"""

from __future__ import annotations

import argparse
import configparser
import re
import sys
from pathlib import Path

CAFE_REPO = Path(r"D:\coding\ccidacafe")
SHORTS_NOTEBOOK_ID = "ed70fc3b-474b-423a-9ca8-d19934703f27"   # 민수대표님_숏폼
# 형식을 지시하지 않는다. 노트북이 가진 포맷을 그대로 쓰게 둔다.
MINIMAL_PROMPT = "이 영상으로 숏폼 스크립트 만들어줘"

SCRIPT_HEADER_RE = re.compile(r"^#{1,4}\s*스크립트\s*$", re.M)
FORMAT_RE = re.compile(r"^#{1,4}\s*선택한 포맷\s*$\s*(.+)$", re.M)


def extract_script(answer: str) -> tuple[str, str]:
    """(나레이션, 노트북이 고른 포맷) 반환."""
    chosen = ""
    m = FORMAT_RE.search(answer)
    if m:
        chosen = m.group(1).strip()

    m = SCRIPT_HEADER_RE.search(answer)
    if not m:
        raise RuntimeError("노트북 응답에 '### 스크립트' 절이 없다. 포맷이 바뀐 것으로 보인다.")
    body = answer[m.end():].strip()

    # 인용부호만 있는 줄이나 홀로 남은 마침표 같은 잔여물을 정리한다.
    lines = [l.rstrip() for l in body.splitlines()]
    lines = [l for l in lines if l.strip() not in (".", "·", "-")]
    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body:
        raise RuntimeError("스크립트 절이 비어 있다.")
    return body, chosen


def fetch(url: str, log=print) -> tuple[str, str]:
    sys.path.insert(0, str(CAFE_REPO))
    import notebooklm_source as n   # noqa: E402

    cfg = configparser.ConfigParser()
    cfg.read(CAFE_REPO / "config.ini", encoding="utf-8")
    c = n.load_config(cfg)
    c["notebook_id"] = SHORTS_NOTEBOOK_ID
    c["prompt"] = MINIMAL_PROMPT
    # 아래 둘은 카페 칼럼용 기본값이라 쇼츠에서는 뒤집어야 한다.
    c["retry_if_no_heading"] = False   # 소제목을 강제하면 나레이션이 그걸 읽는다
    c["strip_promo"] = False           # 노트북이 넣는 CTA 를 홍보로 보고 지운다
    answer = n.fetch_manuscript(url, c, log=log)
    return extract_script(answer)


def main() -> int:
    p = argparse.ArgumentParser(description="숏폼 노트북에서 나레이션 대본 받기")
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True, help="나레이션 저장 경로")
    p.add_argument("--raw-out", help="노트북 원문 저장 경로(분석 파트 포함)")
    args = p.parse_args()

    sys.path.insert(0, str(CAFE_REPO))
    import notebooklm_source as n   # noqa: E402

    cfg = configparser.ConfigParser()
    cfg.read(CAFE_REPO / "config.ini", encoding="utf-8")
    c = n.load_config(cfg)
    c["notebook_id"] = SHORTS_NOTEBOOK_ID
    c["prompt"] = MINIMAL_PROMPT
    c["retry_if_no_heading"] = False
    c["strip_promo"] = False
    answer = n.fetch_manuscript(args.url, c)

    if args.raw_out:
        Path(args.raw_out).write_text(answer, encoding="utf-8")
    script, chosen = extract_script(answer)
    Path(args.out).write_text(script, encoding="utf-8")

    paragraphs = [b for b in script.split("\n\n") if b.strip()]
    print(f"노트북 포맷: {chosen or '표기 없음'}")
    print(f"나레이션 {len(re.sub(r'[  ]', '', script))}자(공백제외) / {len(paragraphs)}문단 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
