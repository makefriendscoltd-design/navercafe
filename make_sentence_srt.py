"""타이밍 마스터 manifest 로 문장 단위 SRT 를 만든다.

사용자 기준: "자막을 문장단위로라도 끊어주면 좋음 너무 긴거는 빼는데
그러니까 줄바꿈 되지 않는 선으로"

그래서 문장을 통째로 한 블록에 넣되, 줄바꿈이 생기는 길이면 절 단위로
자른다. 한 줄에 들어가는 최대치는 폰트로 실측해서 정한다
(Cafe24 Ohsquare 118px, 가용 폭 2000px -> 한글 18자).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SENTENCE_END_RE = re.compile(r"(?<=[.!?。])\s+")
DEFAULT_MAX_CHARS = 18
# 화면에 마침표/쉼표를 띄우지 않는다. 문장 경계는 위에서 이미 잡았으므로
# 자막 텍스트에서는 걷어낸다. synth_minsoo_elevenlabs.clean_caption_text 와 같은 집합.
PUNCT_RE = re.compile(r"[,，、.。:;!?！？]")


def strip_punctuation(text: str) -> str:
    return re.sub(r"\s+", " ", PUNCT_RE.sub("", text)).strip()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END_RE.split(text.strip()) if s.strip()]


def split_to_width(sentence: str, max_chars: int) -> list[str]:
    """문장이 한 줄을 넘치면 어절 경계에서 자른다. 어절은 쪼개지 않는다.

    욕심껏 채우면 마지막 조각이 "바꿉니다." 처럼 한 마디만 남아 보기 나쁘다.
    필요한 조각 수를 먼저 정하고 그 수에 맞춰 고르게 나눈다.
    """
    if len(sentence) <= max_chars:
        return [sentence]
    words = sentence.split()
    if len(words) == 1:
        return [sentence]

    for parts in range(2, len(words) + 1):
        target = len(sentence) / parts
        chunks: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            room_left = parts - len(chunks) - 1
            words_left = len(words) - sum(len(c.split()) for c in chunks) - len(current.split()) - 1
            over_target = current and len(candidate) > target and room_left > 0 and words_left >= room_left
            if over_target or (current and len(candidate) > max_chars):
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        if len(chunks) <= parts and all(len(c) <= max_chars for c in chunks):
            return chunks
    return [sentence]


def timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_blocks(
    phrases: list[dict], max_chars: int, mode: str = "word"
) -> list[tuple[float, float, str]]:
    blocks: list[tuple[float, float, str]] = []
    for phrase in phrases:
        start = float(phrase.get("start", 0.0))
        end = float(phrase.get("end", start))
        text = str(phrase.get("text", ""))
        if mode == "word":
            # 어절 하나가 자막 한 장. "뭐가 위험할까요" 는 "뭐가" / "위험할까요" 로 나뉜다.
            pieces = text.split()
        else:
            pieces = []
            for sentence in split_sentences(text):
                pieces.extend(split_to_width(sentence, max_chars))
        pieces = [p for p in (strip_punctuation(piece) for piece in pieces) if p]
        if not pieces:
            continue
        span = max(end - start, 0.001)
        total = sum(len(p) for p in pieces) or 1
        # 글자 수에 비례해 구간을 나눈다. 읽는 시간이 글자 수를 따라가기 때문.
        cursor = start
        for i, piece in enumerate(pieces):
            share = span * (len(piece) / total)
            piece_end = end if i == len(pieces) - 1 else cursor + share
            blocks.append((cursor, piece_end, piece))
            cursor = piece_end

    # 타이밍 마스터는 오디오 크로스페이드용으로 구문을 0.1초 겹쳐 놓는다.
    # 자막이 그 겹침을 물려받으면 두 장이 동시에 떠서 글자가 맞물려 보인다.
    fixed: list[tuple[float, float, str]] = []
    previous_end = 0.0
    for start, end, text in sorted(blocks, key=lambda b: b[0]):
        start = max(start, previous_end)
        if end <= start:
            end = start + 0.12
        fixed.append((start, end, text))
        previous_end = end
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description="문장 단위 SRT 생성")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--mode", choices=("word", "sentence"), default="word")
    args = parser.parse_args()

    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    blocks = build_blocks(data.get("phrases", []), args.max_chars, args.mode)

    lines: list[str] = []
    for i, (start, end, text) in enumerate(blocks, 1):
        lines.append(str(i))
        lines.append(f"{timestamp(start)} --> {timestamp(end)}")
        lines.append(text)
        lines.append("")
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")

    longest = max((len(b[2]) for b in blocks), default=0)
    average = sum(len(b[2]) for b in blocks) / len(blocks) if blocks else 0
    overlaps = sum(
        1 for a, b in zip(blocks, blocks[1:]) if b[0] < a[1] - 1e-6
    )
    print(
        f"모드 {args.mode} | 블록 {len(blocks)}개 | 최장 {longest}자 | "
        f"평균 {average:.1f}자 | 맞물림 {overlaps}개"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
