from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_time(value: str) -> float:
    h, m, rest = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def fmt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_vtt(path: Path) -> list[tuple[float, float, str]]:
    events: list[tuple[float, float, str]] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        start_text, end_text = [part.strip().split()[0] for part in line.split("-->", 1)]
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
        if text:
            events.append((parse_time(start_text), parse_time(end_text), text))
        i += 1
    return events


def split_text(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Keep the first channel-style phrase together when it reads better as a hook.
    protected = {"이 남자는"}
    if text in protected:
        return [text]
    return [word for word in text.split(" ") if word]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    entries: list[str] = []
    index = 1
    for start, end, text in parse_vtt(args.input):
        units = split_text(text)
        if not units:
            continue
        weights = [max(1, len(re.sub(r"[^\w가-힣]", "", unit))) for unit in units]
        total = sum(weights)
        cursor = start
        for unit_index, (unit, weight) in enumerate(zip(units, weights), start=1):
            unit_end = end if unit_index == len(units) else cursor + (end - start) * weight / total
            entries.extend(
                [
                    str(index),
                    f"{fmt_time(cursor)} --> {fmt_time(unit_end)}",
                    unit,
                    "",
                ]
            )
            index += 1
            cursor = unit_end
    args.output.write_text("\n".join(entries), encoding="utf-8")


if __name__ == "__main__":
    main()
