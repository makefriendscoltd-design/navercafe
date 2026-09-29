"""원본 영상의 자막을 받아 원고의 근거로 쓴다.

지금까지는 NotebookLM에 영상을 넣고 원고를 받아왔다. 그 경로가 2026-09-25부터 사흘
동안 제작을 통째로 세웠다. 화면이 개편되면 버튼을 못 찾고, 후보당 기회가 한 번뿐이라
실패하면 그 영상은 영영 못 쓰고, 브라우저 하나를 잡고 있어서 병렬 제작도 막힌다.

자막은 무료로 받을 수 있고 파일로 남는다. 같은 영상에서 몇 번을 받아도 같은 값이 나오고,
받아둔 뒤에는 브라우저가 필요 없다. 원고는 이 자막을 근거로 구독 에이전트가 쓴다.

자막이 없는 영상은 건너뛴다. 음성인식으로 받아쓰는 것은 그 자체로 또 하나의 실패
지점이라, 자막이 있는 영상이 매일 수십 개씩 들어오는 지금은 필요하지 않다.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

TIMED_LINE = re.compile(
    r"(\d\d):(\d\d):(\d\d)\.(\d\d\d) --> (\d\d):(\d\d):(\d\d)\.(\d\d\d)[^\n]*\n(.*?)(?=\n\n|\Z)",
    re.S,
)
PREFERRED_LANGS = ("ko", "en", "en-US", "en-GB")
# 유튜브는 짧은 시간에 자막을 몰아서 받으면 IP를 막는다. 2026-09-29에 실제로 막혀서
# 열 편 제작이 통째로 멈췄다. 요청 사이에 간격을 두고, 막히면 기다렸다 다시 받는다.
REQUEST_GAP_SECONDS = 4.0
BLOCKED_BACKOFF_SECONDS = (30, 120, 300)
_LAST_REQUEST = [0.0]


class CaptionError(RuntimeError):
    pass


def _seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def parse_vtt(text: str) -> list[dict]:
    """자동자막은 같은 줄을 누적해서 반복한다. 처음 나온 순간만 남긴다."""
    seen: set[str] = set()
    out: list[dict] = []
    for match in TIMED_LINE.finditer(text):
        line = re.sub(r"<[^>]+>", "", match.group(9)).strip().replace("\n", " ")
        line = re.sub(r"\s+", " ", line)
        if not line or line in seen:
            continue
        seen.add(line)
        out.append({"start": round(_seconds(*match.group(1, 2, 3, 4)), 3),
                    "end": round(_seconds(*match.group(5, 6, 7, 8)), 3),
                    "text": line})
    return out


def _throttle() -> None:
    """마지막 요청으로부터 최소 간격을 지킨다. 동시 제작이면 서로 겹친다."""
    import time

    waited = time.monotonic() - _LAST_REQUEST[0]
    if waited < REQUEST_GAP_SECONDS:
        time.sleep(REQUEST_GAP_SECONDS - waited)
    _LAST_REQUEST[0] = time.monotonic()


def _from_api(source_key: str) -> tuple[str, list[dict]] | None:
    """무료 자막 API. yt-dlp보다 먼저 쓴다 - 자막 전용 경로라 429에 덜 걸린다."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    import time

    for attempt, backoff in enumerate((0, *BLOCKED_BACKOFF_SECONDS)):
        if backoff:
            time.sleep(backoff)
        _throttle()
        try:
            fetched = YouTubeTranscriptApi().fetch(source_key, languages=list(PREFERRED_LANGS))
            break
        except Exception as exc:  # noqa: BLE001
            blocked = any(word in type(exc).__name__ for word in ("IpBlocked", "TooManyRequests"))
            if not blocked or attempt == len(BLOCKED_BACKOFF_SECONDS):
                return None
    segments = [{"start": round(float(item.start), 3),
                 "end": round(float(item.start) + float(item.duration), 3),
                 "text": re.sub(r"\s+", " ", item.text).strip()}
                for item in fetched.snippets if item.text.strip()]
    return (getattr(fetched, "language_code", "unknown"), segments) if segments else None


def _from_ytdlp(source_key: str, out_dir: Path) -> tuple[str, list[dict]] | None:
    """API가 막혔을 때 쓰는 예비 경로."""
    import yt_dlp

    from youtube_source_options import source_options

    options = {**source_options(), "skip_download": True, "writeautomaticsub": True,
               "writesubtitles": True, "subtitleslangs": list(PREFERRED_LANGS),
               "subtitlesformat": "vtt", "quiet": True, "no_warnings": True,
               "outtmpl": str(out_dir / "%(id)s.%(ext)s")}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(f"https://youtu.be/{source_key}", download=True)
    except Exception:  # noqa: BLE001
        return None
    for lang in PREFERRED_LANGS:
        path = out_dir / f"{source_key}.{lang}.vtt"
        if path.is_file():
            segments = parse_vtt(path.read_text(encoding="utf-8"))
            if segments:
                return lang, segments
    return None


def fetch(source_key: str, out_dir: Path, *, minutes: float | None = None) -> dict:
    """자막을 받아 `transcript.txt`와 증거를 남기고 요약을 돌려준다."""
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    picked = _from_api(source_key) or _from_ytdlp(source_key, out_dir)
    if picked is None:
        raise CaptionError(f"자막을 받지 못했습니다: {source_key}")
    lang, segments = picked
    if len(segments) < 20:
        raise CaptionError(f"자막 구간이 너무 적습니다({len(segments)}개): {source_key}")

    transcript = "\n".join(item["text"] for item in segments)
    transcript_path = out_dir / "transcript.txt"
    transcript_path.write_text(transcript, encoding="utf-8")
    evidence = {
        "source_key": source_key,
        "url": f"https://youtu.be/{source_key}",
        "language": lang,
        "segment_count": len(segments),
        "spoken_seconds": round(segments[-1]["end"], 3),
        "source_minutes": minutes,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "transcript": str(transcript_path),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_key")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    evidence = fetch(args.source_key, Path(args.out))
    print(json.dumps({k: v for k, v in evidence.items() if k != "transcript"},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
