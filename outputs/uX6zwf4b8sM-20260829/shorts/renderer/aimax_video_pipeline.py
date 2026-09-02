import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "aimax_video_config.json"


@dataclass
class Segment:
    start: float
    end: float
    target_duration: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def output_duration(self) -> float:
        return self.target_duration if self.target_duration is not None else self.duration

    @property
    def speed(self) -> float:
        if self.output_duration <= 0:
            return 1.0
        return self.duration / self.output_duration


def run(cmd, *, capture=False):
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    print(f"[run] {printable}")
    if capture:
        return subprocess.run(cmd, check=True, text=True, capture_output=True)
    subprocess.run(cmd, check=True)
    return None


def require_tool(name: str):
    if shutil.which(name) is None:
        raise SystemExit(f"[error] '{name}' 명령을 찾을 수 없습니다. PATH 설치를 확인하세요.")


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def probe_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def sanitize_stem(path: Path) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", path.stem).strip("_")


def detect_silences(input_video: Path, silence_db: float, min_duration: float):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(input_video),
            "-af",
            f"silencedetect=n={silence_db}dB:d={min_duration}",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    text = result.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    silences = []
    for idx, start in enumerate(starts):
        end = ends[idx] if idx < len(ends) else None
        silences.append((start, end))
    return silences


def build_keep_segments(duration: float, silences, padding: float, min_clip_duration: float):
    cursor = 0.0
    keep = []
    for silence_start, silence_end in silences:
        if silence_end is None:
            silence_end = duration
        end = max(cursor, silence_start + padding)
        if end - cursor >= min_clip_duration:
            keep.append(Segment(cursor, min(duration, end)))
        cursor = max(cursor, silence_end - padding)
    if duration - cursor >= min_clip_duration:
        keep.append(Segment(cursor, duration))
    if not keep:
        keep = [Segment(0.0, duration)]
    return merge_close_segments(keep, min_clip_duration)


def merge_close_segments(segments, min_gap: float):
    merged = []
    for segment in segments:
        if not merged or segment.start - merged[-1].end > min_gap:
            merged.append(segment)
        else:
            merged[-1].end = max(merged[-1].end, segment.end)
    return merged


def next_sequence_path(directory: Path, prefix: str, suffix: str = ".mp4") -> Path:
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+){re.escape(suffix)}$", re.IGNORECASE)
    if directory.exists():
        for item in directory.iterdir():
            if not item.is_file():
                continue
            match = pattern.match(item.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return directory / f"{prefix}_{highest + 1}{suffix}"


def split_long_segments(segments, max_duration: float):
    if max_duration <= 0:
        return segments
    split = []
    for segment in segments:
        cursor = segment.start
        while segment.end - cursor > max_duration:
            split.append(Segment(cursor, cursor + max_duration))
            cursor += max_duration
        if segment.end - cursor > 0:
            split.append(Segment(cursor, segment.end))
    return split


def write_cutlist(path: Path, segments):
    with path.open("w", encoding="utf-8") as f:
        f.write("start,end,source_duration,target_duration,speed\n")
        for segment in segments:
            f.write(
                f"{segment.start:.3f},{segment.end:.3f},{segment.duration:.3f},"
                f"{segment.output_duration:.3f},{segment.speed:.3f}\n"
            )


def atempo_chain(speed: float) -> list[float]:
    speed = max(0.01, speed)
    parts = []
    while speed > 2.0:
        parts.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        parts.append(0.5)
        speed /= 0.5
    parts.append(speed)
    return parts


def cut_video(input_video: Path, output_video: Path, segments):
    filters = []
    labels = []
    for idx, segment in enumerate(segments):
        speed = segment.speed
        filters.append(
            f"[0:v]trim=start={segment.start:.3f}:end={segment.end:.3f},"
            f"setpts=(PTS-STARTPTS)/{speed:.6f}[v{idx}]"
        )
        audio_filter = (
            f"[0:a]atrim=start={segment.start:.3f}:end={segment.end:.3f},"
            "asetpts=PTS-STARTPTS"
        )
        for part in atempo_chain(speed):
            audio_filter += f",atempo={part:.6f}"
        audio_filter += f"[a{idx}]"
        filters.append(audio_filter)
        labels.append(f"[v{idx}][a{idx}]")
    filters.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=1[outv][outa]")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_video),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-r",
            "30",
            *video_encoding_args({}),
            str(output_video),
        ]
    )


def transcribe(video_path: Path, out_dir: Path, model: str, language: str):
    run(
        [
            "whisper",
            str(video_path),
            "--model",
            model,
            "--language",
            language,
            "--output_format",
            "srt",
            "--output_dir",
            str(out_dir),
        ]
    )
    srt_path = out_dir / f"{video_path.stem}.srt"
    if not srt_path.exists():
        matches = sorted(out_dir.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            raise SystemExit("[error] Whisper가 SRT 파일을 생성하지 못했습니다.")
        srt_path = matches[0]
    return srt_path


def parse_srt_time(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_cs = int(round(seconds * 100))
    h = total_cs // 360000
    total_cs %= 360000
    m = total_cs // 6000
    total_cs %= 6000
    s = total_cs // 100
    cs = total_cs % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rgb_hex_to_ass(value: str) -> str:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        return value
    rr, gg, bb = cleaned[0:2], cleaned[2:4], cleaned[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def wrap_ass_caption(text: str, max_chars: int = 15, max_lines: int = 2) -> str:
    words = [word for word in re.split(r"\s+", text.replace(r"\N", " ")) if word]
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        kept = lines[: max_lines - 1]
        kept.append(" ".join(lines[max_lines - 1 :]))
        lines = kept
    return r"\N".join(lines[:max_lines])


def split_caption_text(text: str, max_chars: int) -> list[str]:
    plain = re.sub(r"\s+", " ", text.replace(r"\N", " ")).strip()
    if not plain:
        return []
    chunks: list[str] = []
    current = ""
    for unit in re.split(r"\s+", plain):
        if not unit:
            continue
        candidate = f"{current} {unit}".strip() if current else unit
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_caption_events(
    events: list[tuple[float, float, str]],
    max_chars: int,
) -> list[tuple[float, float, str]]:
    rendered: list[tuple[float, float, str]] = []
    for start, end, caption in events:
        chunks = split_caption_text(caption, max_chars)
        if not chunks:
            continue
        weights = [max(1, len(chunk)) for chunk in chunks]
        total = sum(weights)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            chunk_end = end if index == len(chunks) else min(end, cursor + (end - start) * weight / total)
            rendered.append((cursor, chunk_end, chunk))
            cursor = chunk_end
    return rendered


def merge_short_caption_events(
    events: list[tuple[float, float, str]],
    min_duration: float,
    min_chars: int,
    max_chars: int,
) -> list[tuple[float, float, str]]:
    merged: list[tuple[float, float, str]] = []
    pending: tuple[float, float, str] | None = None

    def char_count(value: str) -> int:
        return len(re.sub(r"\s+", "", value.replace(r"\N", " ")))

    def join_text(left: str, right: str) -> str:
        return re.sub(r"\s+", " ", f"{left} {right}".replace(r"\N", " ")).strip()

    for start, end, caption in events:
        caption = re.sub(r"\s+", " ", caption.replace(r"\N", " ")).strip()
        if not caption:
            continue
        if pending is None:
            pending = (start, end, caption)
            continue

        p_start, p_end, p_caption = pending
        p_short = (p_end - p_start) < min_duration or char_count(p_caption) <= min_chars
        combined = join_text(p_caption, caption)
        if p_short and char_count(combined) <= max_chars:
            if merged:
                prev_start, _, prev_caption = merged[-1]
                prev_combined = join_text(prev_caption, p_caption)
                if char_count(prev_combined) <= max_chars:
                    merged[-1] = (prev_start, p_end, prev_combined)
                    pending = (start, end, caption)
                else:
                    pending = (p_start, end, combined)
            else:
                pending = (p_start, end, combined)
        else:
            merged.append(pending)
            pending = (start, end, caption)

    if pending is not None:
        if merged:
            start, end, caption = pending
            short = (end - start) < min_duration or char_count(caption) <= min_chars
            prev_start, _, prev_caption = merged[-1]
            combined = join_text(prev_caption, caption)
            if short and char_count(combined) <= max_chars:
                merged[-1] = (prev_start, end, combined)
            else:
                merged.append(pending)
        else:
            merged.append(pending)
    return merged


def srt_to_ass(srt_path: Path, ass_path: Path, config: dict):
    sub = config["subtitle"]
    title = config.get("title") or {}
    watermark = config.get("watermark") or {}
    source = config.get("source") or {}
    alignment = int(sub.get("alignment", 2))
    bold = int(sub.get("bold", 0))
    italic = int(sub.get("italic", 0))
    back_color = sub.get("back_color", "&H00000000")
    text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
    text = apply_subtitle_replacements(text, sub.get("replacements") or {})
    blocks = re.split(r"\n\s*\n", text.strip())
    events = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[1].split("-->")]
        caption = r"\N".join(lines[2:]).replace("{", "(").replace("}", ")")
        events.append((parse_srt_time(start_raw), parse_srt_time(end_raw), caption))

    ass = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {config['output_width']}",
        f"PlayResY: {config['output_height']}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,"
        f"{sub['font_name']},{sub['font_size']},{sub['primary_color']},&H000000FF,"
        f"{sub['outline_color']},{back_color},{bold},{italic},0,0,100,100,0,0,1,"
        f"{sub['outline']},{sub['shadow']},{alignment},80,80,{sub['margin_v']},1",
        "Style: Title,"
        f"{title.get('font_name', 'BM HANNA 11yrs old')},{title.get('font_size', 165)},"
        f"{rgb_hex_to_ass(title.get('color', '29D6EA'))},&H000000FF,&H00000000,&H00000000,"
        f"{int(title.get('bold', -1))},{int(title.get('italic', -1))},0,0,100,100,0,0,1,"
        f"{title.get('outline', 0)},{title.get('shadow', 10)},5,80,80,0,1",
        "Style: Watermark,"
        f"{watermark.get('font_name', 'BM HANNA 11yrs old')},{watermark.get('font_size', 88)},"
        f"{rgb_hex_to_ass(watermark.get('color', '29D6EA'))},&H000000FF,&H00000000,&H00000000,"
        f"0,{int(watermark.get('italic', -1))},0,0,100,100,0,0,1,0,{watermark.get('shadow', 8)},5,80,80,0,1",
        "Style: Source,"
        f"{source.get('font_name', 'Arial')},{source.get('font_size', 54)},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,4,5,80,80,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    max_chars = int(sub.get("max_chars_per_line", 15))
    max_lines = int(sub.get("max_lines", 2))
    max_event_chars = max_chars * max_lines
    subtitle_mode = sub.get("mode")
    if subtitle_mode != "eojel" and sub.get("merge_enabled", True):
        events = merge_short_caption_events(
            events,
            min_duration=float(sub.get("merge_min_duration", 0.65)),
            min_chars=int(sub.get("merge_min_chars", 3)),
            max_chars=int(sub.get("merge_max_chars", max_event_chars + 8)),
        )
    if subtitle_mode == "word":
        rendered_events = []
        for start, end, caption in events:
            words = [word for word in re.split(r"\s+", caption.replace(r"\N", " ")) if word]
            if not words:
                continue
            weights = [max(1, len(word)) for word in words]
            total = sum(weights)
            cursor = start
            for word, weight in zip(words, weights):
                duration = max(0.18, (end - start) * weight / total)
                word_end = min(end, cursor + duration)
                rendered_events.append((cursor, word_end, word))
                cursor = word_end
            if rendered_events and rendered_events[-1][1] < end:
                last_start, _, last_word = rendered_events[-1]
                rendered_events[-1] = (last_start, end, last_word)
    elif subtitle_mode == "eojel":
        rendered_events = events
    else:
        rendered_events = split_caption_events(events, max_event_chars)

    caption_x = sub.get("x")
    caption_y = sub.get("y")
    for start, end, caption in rendered_events:
        caption = wrap_ass_caption(caption, max_chars=max_chars, max_lines=max_lines)
        if not caption:
            continue
        if caption_x is not None and caption_y is not None:
            caption = f"{{\\pos({int(caption_x)},{int(caption_y)})}}{caption}"
        ass.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{caption}")

    title_text = str(title.get("text", "")).replace("\n", r"\N")
    if title_text:
        end_time = ass_time(max((end for _, end, _ in events), default=3600.0))
        ass.append(
            f"Dialogue: 1,0:00:00.00,{end_time},Title,,0,0,0,,"
            f"{{\\pos({int(title.get('x', config['output_width'] // 2))},{int(title.get('y', 880))})}}{title_text}"
        )
    watermark_text = str(watermark.get("text", ""))
    if watermark_text:
        end_time = ass_time(max((end for _, end, _ in events), default=3600.0))
        ass.append(
            f"Dialogue: 1,0:00:00.00,{end_time},Watermark,,0,0,0,,"
            f"{{\\pos({int(watermark.get('x', config['output_width'] // 2))},{int(watermark.get('y', 3535))})}}{watermark_text}"
        )
    source_text = str(source.get("text", ""))
    if source_text:
        ass.append(
            f"Dialogue: 1,0:00:00.00,{ass_time(float(source.get('duration', 3.0)))},Source,,0,0,0,,"
            f"{{\\pos({int(source.get('x', config['output_width'] // 2))},{int(source.get('y', 2540))})}}{source_text}"
        )
    ass_path.write_text("\n".join(ass) + "\n", encoding="utf-8")


def srt_plain_text(srt_path: Path) -> str:
    text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines)


def generate_headline(srt_path: Path) -> str:
    text = srt_plain_text(srt_path)
    if "클로드" in text and "편집" in text:
        if "10분" in text:
            return "편집자들 망하겠네...\n클로드로 편집 10분 컷?!"
        return "편집자들 긴장할듯...\n클로드로 영상 자동 편집"
    if "100명" in text and "직원" in text:
        return "100명 직원 다 짜름\n2026년 가장 값진 스킬"
    if "AI" in text and "자동화" in text:
        return "직원 없이도 돌아간다\nAI 자동화 핵심 전략"
    if "10배" in text:
        return "속도 10배 올라간다\n지금 바로 써먹는 방법"
    return "지금 모르면 늦습니다\nAI로 바뀌는 작업 방식"


def resolve_title(config: dict, corrected_srt: Path):
    title = config.get("title") or {}
    if title.get("auto") or str(title.get("text", "")).strip().lower() == "auto":
        title["text"] = generate_headline(corrected_srt)
        config["title"] = title


def apply_subtitle_replacements(text: str, replacements: dict[str, str]) -> str:
    for wrong, right in replacements.items():
        text = text.replace(wrong, right)
    return text


def write_corrected_srt(source_srt: Path, corrected_srt: Path, config: dict):
    sub = config.get("subtitle") or {}
    text = source_srt.read_text(encoding="utf-8-sig", errors="replace")
    text = apply_subtitle_replacements(text, sub.get("replacements") or {})
    corrected_srt.write_text(text, encoding="utf-8")


def ffmpeg_subtitle_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:", value):
        value = value[0] + r"\:" + value[2:]
    return value.replace("'", r"\'")


def subtitles_filter(ass_path: Path, config: dict) -> str:
    subtitle_cfg = config.get("subtitle") or {}
    parts = [f"'{ffmpeg_subtitle_path(ass_path)}'"]
    font_path = subtitle_cfg.get("font_path")
    if font_path and Path(font_path).exists():
        parts.append(f"fontsdir='{ffmpeg_path(str(Path(font_path).parent))}'")
    return "subtitles=" + ":".join(parts)


def ffmpeg_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("\n", r"\n")
    )


def ffmpeg_path(path: str) -> str:
    value = Path(path).resolve().as_posix()
    if re.match(r"^[A-Za-z]:", value):
        value = value[0] + r"\:" + value[2:]
    return value.replace("'", r"\'")


def as_existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path if path.exists() else None


def micros_to_seconds(value: int | float | None) -> float:
    return float(value or 0) / 1_000_000.0


def find_latest_capcut_draft() -> Path | None:
    root = Path.home() / "AppData" / "Local" / "CapCut Drafts"
    if not root.exists():
        return None
    matches = sorted(root.rglob("draft_content.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in matches:
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        materials = material_index(draft)
        for track in draft.get("tracks") or []:
            if track.get("type") != "video" or track.get("flag") != 0 or not track.get("segments"):
                continue
            material = materials.get(track["segments"][0].get("material_id"), {})
            raw_path = material.get("path") or ""
            if raw_path and Path(raw_path).exists():
                return path
    return matches[0] if matches else None


def material_index(draft: dict) -> dict[str, dict]:
    indexed = {}
    for items in (draft.get("materials") or {}).values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                indexed[item["id"]] = item
    return indexed


def text_content(material: dict) -> dict:
    content = material.get("content") or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def color_list_to_hex(value, fallback="29D6EA") -> str:
    if not isinstance(value, list) or len(value) < 3:
        return fallback
    return "".join(f"{max(0, min(255, round(float(channel) * 255))):02X}" for channel in value[:3])


def font_name_from_path(path: str, fallback: str) -> str:
    lowered = path.replace("\\", "/").lower()
    if "bmhanna" in lowered:
        return "BM HANNA 11yrs old"
    if "cafe24ohsquare" in lowered:
        return "Cafe24 Ohsquare"
    return fallback


def capcut_position(clip: dict, width: int, height: int) -> tuple[int, int]:
    transform = ((clip or {}).get("transform") or {})
    x = int(round((0.5 + float(transform.get("x", 0.0)) / 2.0) * width))
    y = int(round((0.5 - float(transform.get("y", 0.0)) / 2.0) * height))
    return x, y


def scaled_text_size(style: dict, segment: dict, multiplier: float) -> int:
    clip_scale = (((segment.get("clip") or {}).get("scale") or {}).get("x")) or 1.0
    return int(round(float(style.get("size", 10.0)) * float(clip_scale) * multiplier))


def apply_capcut_text(config: dict, key: str, segment: dict, material: dict, multiplier: float):
    content = text_content(material)
    styles = content.get("styles") or [{}]
    style = styles[0] if styles else {}
    fill = (((style.get("fill") or {}).get("content") or {}).get("solid") or {})
    font = style.get("font") or {}
    font_path = font.get("path") or ""
    x, y = capcut_position(segment.get("clip") or {}, int(config["output_width"]), int(config["output_height"]))
    target = config.setdefault(key, {})
    target["text"] = content.get("text", target.get("text", ""))
    target["font_path"] = font_path or target.get("font_path", "")
    target["font_name"] = font_name_from_path(font_path, target.get("font_name", "Arial"))
    target["font_size"] = scaled_text_size(style, segment, multiplier)
    target["color"] = color_list_to_hex(fill.get("color"), target.get("color", "FFFFFF"))
    target["x"] = x
    target["y"] = y
    if style.get("italic"):
        target["italic"] = -1
    duration = micros_to_seconds((segment.get("target_timerange") or {}).get("duration"))
    if key == "source" and duration:
        target["duration"] = duration
    return target


def capcut_template_segments(draft: dict) -> list[Segment]:
    segments = []
    for track in draft.get("tracks") or []:
        if track.get("type") != "video" or track.get("flag") != 0:
            continue
        for segment in track.get("segments") or []:
            source = segment.get("source_timerange") or {}
            target = segment.get("target_timerange") or {}
            start = micros_to_seconds(source.get("start"))
            source_duration = micros_to_seconds(source.get("duration"))
            target_duration = micros_to_seconds(target.get("duration"))
            if source_duration > 0 and target_duration > 0:
                segments.append(Segment(start, start + source_duration, target_duration))
        if segments:
            break
    return segments


def flatten_targeted_segments(raw_segments: list[tuple[float, float, float, float]]) -> list[Segment]:
    flattened = []
    cursor = 0.0
    for target_start, target_duration, source_start, source_duration in sorted(raw_segments):
        target_end = target_start + target_duration
        if target_end <= cursor:
            continue
        overlap = max(0.0, cursor - target_start)
        adjusted_source_start = source_start + min(overlap, source_duration)
        adjusted_source_duration = max(0.0, source_duration - overlap)
        adjusted_target_duration = max(0.0, target_end - max(cursor, target_start))
        adjusted_source_duration = min(adjusted_source_duration, adjusted_target_duration)
        if adjusted_source_duration > 0.03 and adjusted_target_duration > 0.03:
            flattened.append(
                Segment(
                    adjusted_source_start,
                    adjusted_source_start + adjusted_source_duration,
                    adjusted_target_duration,
                )
            )
        cursor = max(cursor, target_end)
    return flattened


def nested_presenter_template(draft: dict) -> tuple[Path | None, list[Segment]]:
    for combo in (draft.get("materials") or {}).get("drafts") or []:
        nested = combo.get("draft") or {}
        nested_materials = material_index(nested)
        raw_segments = []
        presenter_path = None
        for track in nested.get("tracks") or []:
            if track.get("type") != "video":
                continue
            for segment in track.get("segments") or []:
                material = nested_materials.get(segment.get("material_id"), {})
                raw_path = material.get("path") or ""
                if not raw_path:
                    continue
                path = Path(raw_path)
                if not path.exists():
                    continue
                presenter_path = presenter_path or path
                source = segment.get("source_timerange") or {}
                target = segment.get("target_timerange") or {}
                raw_segments.append(
                    (
                        micros_to_seconds(target.get("start")),
                        micros_to_seconds(target.get("duration")),
                        micros_to_seconds(source.get("start")),
                        micros_to_seconds(source.get("duration")),
                    )
                )
        if presenter_path and raw_segments:
            return presenter_path, flatten_targeted_segments(raw_segments)
    return None, []


def capcut_caption_events(draft: dict, materials: dict[str, dict]) -> list[tuple[float, float, str]]:
    events = []
    for track in draft.get("tracks") or []:
        if track.get("type") != "text" or len(track.get("segments") or []) <= 10:
            continue
        for segment in track.get("segments") or []:
            target = segment.get("target_timerange") or {}
            start = micros_to_seconds(target.get("start"))
            end = start + micros_to_seconds(target.get("duration"))
            material = materials.get(segment.get("material_id"), {})
            text = str(text_content(material).get("text") or "").strip()
            if text and end > start:
                events.append((start, end, text))
        break
    return sorted(events, key=lambda item: item[0])


def write_srt_events(path: Path, events: list[tuple[float, float, str]]):
    lines = []
    for idx, (start, end, text) in enumerate(events, start=1):
        lines.append(str(idx))
        lines.append(f"{srt_time(start)} --> {srt_time(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - math.floor(seconds)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def apply_capcut_template(config: dict, draft_path: Path) -> Path | None:
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    materials = material_index(draft)
    width = int(config["output_width"])
    height = int(config["output_height"])
    config["fps"] = int(round(float(draft.get("fps") or config.get("fps") or 30)))
    config["_capcut_template"] = str(draft_path)
    config["export"] = {
        "video_codec": "libx264",
        "video_bitrate": "9M",
        "preset": "veryfast",
        "audio_bitrate": "192k",
        "x265_params": "log-level=error",
    }

    talking_path = None
    screen_path = None
    for track in draft.get("tracks") or []:
        if track.get("type") == "video" and track.get("flag") == 0 and track.get("segments"):
            material = materials.get(track["segments"][0].get("material_id"), {})
            if material.get("path"):
                screen_path = Path(material["path"])
            break
    presenter_path, presenter_segments = nested_presenter_template(draft)
    if presenter_path:
        talking_path = presenter_path
    if screen_path and screen_path.exists():
        config["_template_screen_path"] = str(screen_path)

    text_tracks = [track for track in draft.get("tracks") or [] if track.get("type") == "text"]
    for key, index, multiplier in [("title", 0, 13.6), ("source", 1, 10.8), ("watermark", 2, 12.65)]:
        if len(text_tracks) <= index or not text_tracks[index].get("segments"):
            continue
        segment = text_tracks[index]["segments"][0]
        material = materials.get(segment.get("material_id"), {})
        apply_capcut_text(config, key, segment, material, multiplier)
    if config.get("title"):
        config["title"]["auto"] = False

    caption_tracks = [track for track in text_tracks if len(track.get("segments") or []) > 10]
    if caption_tracks and config.setdefault("subtitle", {}).get("import_template_style"):
        first = caption_tracks[0]["segments"][0]
        first_material = materials.get(first.get("material_id"), {})
        content = text_content(first_material)
        style = (content.get("styles") or [{}])[0]
        font_path = ((style.get("font") or {}).get("path") or "")
        subtitle = config.setdefault("subtitle", {})
        subtitle["font_path"] = font_path or subtitle.get("font_path", "")
        subtitle["font_name"] = font_name_from_path(font_path, subtitle.get("font_name", "Cafe24 Ohsquare"))
        subtitle["font_size"] = int(round(float(style.get("size", 13.0)) * 9.7))
        subtitle["italic"] = -1 if style.get("italic") else subtitle.get("italic", 0)
        subtitle["outline"] = 0
        subtitle["shadow"] = 9
        subtitle["alignment"] = 5
        subtitle["mode"] = "word"

    effects = []
    music_segments = []
    for track in draft.get("tracks") or []:
        if track.get("type") != "audio":
            continue
        for segment in track.get("segments") or []:
            material = materials.get(segment.get("material_id"), {})
            path = material.get("path")
            if not path:
                continue
            start = micros_to_seconds((segment.get("target_timerange") or {}).get("start"))
            duration = micros_to_seconds((segment.get("target_timerange") or {}).get("duration"))
            volume_value = segment.get("volume", 1.0)
            if isinstance(volume_value, dict):
                volume = float(volume_value.get("volume", 1.0))
            else:
                volume = float(volume_value)
            if duration > 5.0:
                music_segments.append({"path": path, "start": start, "volume": volume})
            else:
                effects.append({"path": path, "start": round(start, 3), "volume": volume})
    audio = config.setdefault("audio", {})
    if music_segments:
        audio["music_path"] = music_segments[0]["path"]
        audio["music_volume"] = music_segments[0]["volume"]
    if effects:
        audio["effects"] = sorted(effects, key=lambda item: item["start"])

    screen_segments = capcut_template_segments(draft)
    if screen_segments:
        config["_template_screen_segments"] = [
            {"start": s.start, "end": s.end, "target_duration": s.target_duration} for s in screen_segments
        ]
        config.setdefault("screen", {})["speed"] = 1.0
    segments = presenter_segments or screen_segments
    if segments:
        config["_template_cut_segments"] = [
            {"start": s.start, "end": s.end, "target_duration": s.target_duration} for s in segments
        ]
    captions = capcut_caption_events(draft, materials)
    if captions:
        config["_template_captions"] = [
            {"start": start, "end": end, "text": text} for start, end, text in captions
        ]
        config.setdefault("subtitle", {})["mode"] = "caption"

    smart_relights = (draft.get("materials") or {}).get("smart_relights") or []
    if smart_relights:
        presenter = config.setdefault("presenter", {})
        presenter["capcut_smart_relight"] = smart_relights[0]
        presenter["x"] = 650
        presenter["y"] = 2595
        presenter["width"] = 860
        presenter["height"] = 860
        presenter["color_filter"] = "eq=brightness=0.025:contrast=1.14:saturation=1.06,colorbalance=bs=0.035:gs=0.01"
        presenter["left_light"] = {"color": "00C8FF", "alpha": 0.38, "width": 520}

    return talking_path if talking_path and talking_path.exists() else None


def template_segments(config: dict) -> list[Segment]:
    raw_segments = config.get("_template_cut_segments") or []
    return [
        Segment(float(item["start"]), float(item["end"]), float(item["target_duration"]))
        for item in raw_segments
    ]


def named_template_segments(config: dict, key: str) -> list[Segment]:
    return [
        Segment(float(item["start"]), float(item["end"]), float(item["target_duration"]))
        for item in (config.get(key) or [])
    ]


def template_caption_events(config: dict) -> list[tuple[float, float, str]]:
    return [
        (float(item["start"]), float(item["end"]), str(item["text"]))
        for item in (config.get("_template_captions") or [])
    ]


def video_encoding_args(config: dict) -> list[str]:
    export = config.get("export") or {}
    codec = export.get("video_codec", "libx264")
    args = ["-c:v", codec, "-preset", export.get("preset", "veryfast")]
    # x264 allocates frame buffers per thread, and at 2160x3840 the default
    # (one per core) can exhaust RAM mid-encode — it fails with
    # "malloc of size ... failed" after minutes of work. Cap it when the
    # machine is tight; unset keeps ffmpeg's default.
    if export.get("threads"):
        args.extend(["-threads", str(export["threads"])])
    if export.get("video_bitrate"):
        args.extend(["-b:v", str(export["video_bitrate"]), "-maxrate", str(export["video_bitrate"]), "-bufsize", "18M"])
    else:
        args.extend(["-crf", str(export.get("crf", 18))])
    if codec == "libx265" and export.get("x265_params"):
        args.extend(["-x265-params", str(export["x265_params"])])
    args.extend(["-c:a", "aac", "-b:a", str(export.get("audio_bitrate", "192k")), "-ac", "2", "-ar", "48000"])
    return args


def audio_master_filter(input_label: str, output_label: str, audio_cfg: dict) -> str:
    lufs = float(audio_cfg.get("master_lufs", -14.0))
    lra = float(audio_cfg.get("master_lra", 3.0))
    true_peak = float(audio_cfg.get("master_true_peak", -1.8))
    limiter = 10 ** (true_peak / 20.0)
    return (
        f"[{input_label}]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"loudnorm=I={lufs:g}:LRA={lra:g}:TP={true_peak:g}:linear=true,"
        f"alimiter=limit={limiter:.4f}:level=false,aresample=48000[{output_label}]"
    )


def drawtext_filter(text_config: dict) -> str:
    fontfile = ffmpeg_path(text_config["font_path"])
    lines = str(text_config["text"]).splitlines() or [""]
    font_size = int(text_config["font_size"])
    color = text_config["color"]
    x = int(text_config["x"])
    y = int(text_config["y"])
    line_spacing = int(text_config.get("line_spacing", 0))
    borderw = int(text_config.get("outline", 0))
    shadow = int(text_config.get("shadow", 5))
    line_step = font_size + line_spacing
    first_y = y - ((len(lines) - 1) * line_step / 2)
    filters = []
    for idx, line in enumerate(lines):
        text = ffmpeg_text(line)
        line_y = first_y + idx * line_step
        filters.append(
            "drawtext="
            f"fontfile='{fontfile}':"
            f"text='{text}':"
            f"fontcolor=0x{color}:fontsize={font_size}:"
            f"x={x}-(text_w/2):y={line_y:.1f}-(text_h/2):"
            f"bordercolor=black@0.85:borderw={borderw}:"
            f"shadowcolor=black@0.9:shadowx={shadow}:shadowy={shadow}"
        )
    return ",".join(filters)


def text_overlay_filter(
    input_label: str,
    output_label: str,
    text_config: dict,
    width: int,
    height: int,
    fps: int,
    prefix: str,
) -> str:
    shear = float(text_config.get("shear", 0.0))
    if abs(shear) < 0.001:
        return f"[{input_label}]{drawtext_filter(text_config)}[{output_label}]"
    return (
        f"color=c=black@0.0:s={width}x{height}:r={fps},format=rgba[{prefix}base];"
        f"[{prefix}base]{drawtext_filter(text_config)}[{prefix}drawn];"
        f"[{prefix}drawn]shear=shx={shear}:shy=0:fillcolor=black@0,format=rgba[{prefix}sheared];"
        f"[{input_label}][{prefix}sheared]overlay=0:0:format=auto[{output_label}]"
    )


def render_vertical_aimax(
    edited_video: Path,
    ass_path: Path,
    output_video: Path,
    config: dict,
    screen_video: Path | None,
    voiceover: Path | None = None,
):
    if not screen_video:
        raise SystemExit("[error] vertical_aimax 레이아웃은 --screen 영상이 필요합니다.")

    width = int(config["output_width"])
    height = int(config["output_height"])
    presenter = config["presenter"]
    screen = config["screen"]

    pw, ph = int(presenter["width"]), int(presenter["height"])
    px, py = int(presenter["x"]), int(presenter["y"])
    radius = min(pw, ph) // 2
    cx, cy = pw // 2, ph // 2
    color_filter = presenter.get("color_filter", "").strip()
    talk_filters = [
        f"scale={pw}:{ph}:force_original_aspect_ratio=increase",
        f"crop={pw}:{ph}",
        "format=rgba",
    ]
    if color_filter:
        talk_filters.append(color_filter)
    if presenter.get("shape") == "circle":
        mask_filter = (
            "geq="
            "r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lte((X-{cx})*(X-{cx})+(Y-{cy})*(Y-{cy}),{radius}*{radius}),255,0)'"
        )
    else:
        mask_filter = "null"

    sw, sh = int(screen["width"]), int(screen["height"])
    sx, sy = int(screen["x"]), int(screen["y"])
    screen_speed = float(screen.get("speed", 1.0))
    speed_filter = f",setpts=PTS/{screen_speed:g}" if abs(screen_speed - 1.0) > 0.001 else ""

    sub_filter = subtitles_filter(ass_path, config)
    filter_complex = (
        f"color=c=black:s={width}x{height}:r={config['fps']}[base];"
        f"[1:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},setsar=1{speed_filter}[screen];"
        f"[0:v]{','.join(talk_filters)}[talk0];"
    )

    left_light = presenter.get("left_light") or {}
    if left_light:
        light_alpha = float(left_light.get("alpha", 0.25))
        light_width = max(1, int(left_light.get("width", pw)))
        light_color = left_light.get("color", "29D6EA")
        filter_complex += (
            f"color=c=0x{light_color}:s={pw}x{ph}:r={config['fps']},format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lte((X-{cx})*(X-{cx})+(Y-{cy})*(Y-{cy}),{radius}*{radius}),"
            f"255*{light_alpha}*max(0,1-X/{light_width}),0)'[relight];"
            "[talk0][relight]overlay=0:0:format=auto[talklit];"
            f"[talklit]{mask_filter}[talk];"
        )
    else:
        filter_complex += f"[talk0]{mask_filter}[talk];"

    filter_complex += (
        f"[base][screen]overlay={sx}:{sy}[v1];"
        f"[v1][talk]overlay={px}:{py}:format=auto[v2];"
        f"[v2]format=yuv420p,{sub_filter}[outv]"
    )

    cmd = ["ffmpeg", "-hide_banner", "-y"]
    # The presenter clip is a fixed take, so a script longer than it used to be
    # truncated by -shortest — the narration's last seconds, CTA included, were
    # simply missing from the render. Loop it like the screen layer does.
    # Only safe when a voiceover bounds the output: without one the audio comes
    # from this input, and looping every stream would leave -shortest nothing
    # finite to stop on.
    if voiceover:
        cmd.extend(["-stream_loop", "-1"])
    cmd.extend([
            "-i",
            str(edited_video),
            "-ss",
            f"{float(screen.get('start_at', 0.0)):.3f}",
            "-stream_loop",
            "-1",
            "-i",
            str(screen_video),
    ])

    audio_map = "0:a"
    audio_cfg = config.get("audio") or {}
    music_path = audio_cfg.get("music_path")
    audio_labels = []
    audio_mix_filters = []
    next_input_index = 2

    if voiceover:
        cmd.extend(["-i", str(voiceover)])
        voice_volume = float(audio_cfg.get("voice_volume", 1.0))
        audio_mix_filters.append(
            f"[{next_input_index}:a]volume={voice_volume},"
            "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[a0]"
        )
        next_input_index += 1
        audio_map = "[a0]"
    else:
        audio_mix_filters.append("[0:a]volume=1.0,aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[a0]")
    audio_labels.append("[a0]")

    music_file = as_existing_path(music_path)
    if music_file:
        cmd.extend(["-stream_loop", "-1", "-i", str(music_file)])
        music_volume = float(audio_cfg.get("music_volume", 0.08)) * float(audio_cfg.get("music_volume_scale", 1.0))
        audio_mix_filters.append(
            f"[{next_input_index}:a]volume={music_volume},"
            "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[music]"
        )
        audio_labels.append("[music]")
        next_input_index += 1

    for idx, effect in enumerate(audio_cfg.get("effects") or []):
        effect_file = as_existing_path(effect.get("path"))
        if not effect_file:
            continue
        cmd.extend(["-i", str(effect_file)])
        start_ms = int(round(float(effect.get("start", 0.0)) * 1000))
        volume = float(effect.get("volume", 0.7)) * float(audio_cfg.get("sfx_volume_scale", 1.0))
        label = f"sfx{idx}"
        audio_mix_filters.append(
            f"[{next_input_index}:a]volume={volume},aresample=48000,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,adelay={start_ms}|{start_ms}[{label}]"
        )
        audio_labels.append(f"[{label}]")
        next_input_index += 1

    if len(audio_labels) > 1:
        filter_complex += ";" + ";".join(audio_mix_filters)
        filter_complex += (
            f";{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
            "duration=first:dropout_transition=0:normalize=0[mix0];"
            + audio_master_filter("mix0", "aout", audio_cfg)
        )
        audio_map = "[aout]"
    elif audio_map.startswith("["):
        filter_complex += ";" + ";".join(audio_mix_filters)
        filter_complex += ";" + audio_master_filter("a0", "aout", audio_cfg)
        audio_map = "[aout]"

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            audio_map,
            "-shortest",
            "-r",
            str(config["fps"]),
            *video_encoding_args(config),
            str(output_video),
        ]
    )
    run(cmd)


def render_final(
    edited_video: Path,
    ass_path: Path,
    output_video: Path,
    config: dict,
    screen_video: Path | None,
    voiceover: Path | None = None,
):
    if config.get("layout") == "vertical_aimax":
        render_vertical_aimax(edited_video, ass_path, output_video, config, screen_video, voiceover)
        return

    width = int(config["output_width"])
    height = int(config["output_height"])
    presenter = config["presenter"]
    px, py = int(presenter["x"]), int(presenter["y"])
    pw, ph = int(presenter["width"]), int(presenter["height"])
    crop = presenter.get("crop", "").strip()
    color_filter = presenter.get("color_filter", "").strip()
    presenter_filters = []
    if crop:
        presenter_filters.append(f"crop={crop}")
    presenter_filters.append(f"scale={pw}:{ph}:force_original_aspect_ratio=increase")
    presenter_filters.append(f"crop={pw}:{ph}")
    if color_filter:
        presenter_filters.append(color_filter)

    sub_filter = subtitles_filter(ass_path, config)
    if screen_video:
        screen_cfg = config.get("screen", {})
        screen_speed = float(screen_cfg.get("speed", 1.0))
        screen_start = float(screen_cfg.get("start_at", 0.0))
        speed_filter = f",setpts=PTS/{screen_speed:g}" if abs(screen_speed - 1.0) > 0.001 else ""
        screen_filters = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1{speed_filter}[bg]"
        )
        filter_complex = (
            f"[0:v]{','.join(presenter_filters)}[talk];"
            f"{screen_filters};"
            f"[bg][talk]overlay={px}:{py}:format=auto,format=yuv420p,{sub_filter}[outv]"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(edited_video),
            "-ss",
            f"{screen_start:.3f}",
            "-stream_loop",
            "-1",
            "-i",
            str(screen_video),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "0:a",
            "-shortest",
            "-r",
            str(config["fps"]),
            *video_encoding_args(config),
            str(output_video),
        ]
    else:
        filter_complex = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,{sub_filter}[outv]"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(edited_video),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "0:a",
            "-r",
            str(config["fps"]),
            *video_encoding_args(config),
            str(output_video),
        ]
    run(cmd)


def main():
    parser = argparse.ArgumentParser(description="AIMAX 반복 영상 자동 편집 파이프라인")
    parser.add_argument("--talking", type=Path, help="presenter recording mp4")
    parser.add_argument("--screen", type=Path, help="배경/원본 유튜브 화면 녹화 mp4")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs")
    parser.add_argument("--final-dir", type=Path, help="copy final upload mp4 to this directory")
    parser.add_argument("--final-prefix", default="minsoo_aimax", help="sequence filename prefix for --final-dir")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--capcut-template", type=str, help="CapCut draft_content.json path, or latest")
    parser.add_argument("--use-template-talking", action="store_true")
    parser.add_argument("--disable-template-cuts", action="store_true")
    parser.add_argument("--disable-template-screen-cuts", action="store_true")
    parser.add_argument("--voiceover", type=Path, help="generated narration wav/mp3")
    parser.add_argument("--title-text", help="override top headline text; use \\n for line breaks")
    parser.add_argument("--title-font-size", type=int, help="override top headline font size")
    parser.add_argument("--source-text", help="override source credit text")
    parser.add_argument("--voice-volume", type=float, help="voiceover volume multiplier")
    parser.add_argument("--music-volume-scale", type=float, help="background music volume multiplier")
    parser.add_argument("--sfx-volume-scale", type=float, help="sound effect volume multiplier")
    parser.add_argument("--skip-whisper", action="store_true")
    parser.add_argument("--srt", type=Path, help="이미 만든 SRT가 있으면 지정")
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    config = load_config(args.config)
    template_talking = None
    if args.capcut_template:
        if args.capcut_template.lower() == "latest":
            template_path = find_latest_capcut_draft()
            if not template_path:
                raise SystemExit("[error] CapCut draft_content.json not found")
        else:
            template_path = Path(args.capcut_template).resolve()
        if not template_path.exists():
            raise SystemExit(f"[error] CapCut template not found: {template_path}")
        template_talking = apply_capcut_template(config, template_path)
        print(f"[info] capcut template: {template_path}")
        if template_talking:
            print(f"[info] template talking: {template_talking}")

    if args.title_text:
        title_cfg = config.setdefault("title", {})
        title_cfg["text"] = args.title_text.replace("\\n", "\n")
        title_cfg["auto"] = False
    if args.title_font_size:
        config.setdefault("title", {})["font_size"] = args.title_font_size
    if args.source_text:
        config.setdefault("source", {})["text"] = args.source_text
    audio_cfg = config.setdefault("audio", {})
    if args.voice_volume is not None:
        audio_cfg["voice_volume"] = args.voice_volume
    if args.music_volume_scale is not None:
        audio_cfg["music_volume_scale"] = args.music_volume_scale
    if args.sfx_volume_scale is not None:
        audio_cfg["sfx_volume_scale"] = args.sfx_volume_scale

    talking_arg = template_talking if args.use_template_talking and template_talking else args.talking
    if not talking_arg:
        raise SystemExit("[error] --talking is required unless --capcut-template with --use-template-talking finds one")
    talking = talking_arg.resolve()
    screen = args.screen.resolve() if args.screen else None
    voiceover = args.voiceover.resolve() if args.voiceover else None
    if not screen and config.get("_template_screen_path"):
        screen = Path(config["_template_screen_path"]).resolve()
        print(f"[info] template screen: {screen}")
    if not talking.exists():
        raise SystemExit(f"[error] talking 파일이 없습니다: {talking}")
    if screen and not screen.exists():
        raise SystemExit(f"[error] screen 파일이 없습니다: {screen}")
    if voiceover and not voiceover.exists():
        raise SystemExit(f"[error] voiceover 파일이 없습니다: {voiceover}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_stem(talking)
    work_dir = args.out_dir / stem
    work_dir.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(talking)
    print(f"[info] input duration: {duration:.2f}s")
    segments = []
    if args.capcut_template and not args.disable_template_cuts:
        segments = template_segments(config)
        if segments:
            print(f"[info] template cut segments: {len(segments)}")
    if not segments:
        silences = detect_silences(talking, config["silence_db"], config["silence_min_duration"])
        segments = build_keep_segments(
            duration,
            silences,
            float(config["keep_padding"]),
            float(config["min_clip_duration"]),
        )
        segments = split_long_segments(segments, float(config.get("max_segment_duration", 0.0)))
    write_cutlist(work_dir / "cutlist.csv", segments)
    print(f"[info] keep segments: {len(segments)}")

    edited = work_dir / f"{stem}_edited.mp4"
    cut_video(talking, edited, segments)
    render_screen = screen
    screen_segments = [] if args.disable_template_screen_cuts else named_template_segments(config, "_template_screen_segments")
    if screen and screen_segments:
        screen_stem = sanitize_stem(screen)
        screen_edited = work_dir / f"{screen_stem}_screen_edited.mp4"
        cut_video(screen, screen_edited, screen_segments)
        render_screen = screen_edited

    template_srt = work_dir / f"{stem}_template.srt"
    template_captions = template_caption_events(config)
    if args.srt:
        srt_path = args.srt.resolve()
    elif template_captions:
        write_srt_events(template_srt, template_captions)
        srt_path = template_srt
        print(f"[info] template captions: {len(template_captions)}")
    elif args.skip_whisper:
        raise SystemExit("[error] --skip-whisper 사용 시 --srt 파일이 필요합니다.")
    else:
        require_tool("whisper")
        srt_path = transcribe(edited, work_dir, config["whisper_model"], config["whisper_language"])

    ass_path = work_dir / f"{stem}.ass"
    corrected_srt = work_dir / f"{stem}_corrected.srt"
    write_corrected_srt(srt_path, corrected_srt, config)
    resolve_title(config, corrected_srt)
    srt_to_ass(corrected_srt, ass_path, config)

    final = work_dir / f"{stem}_final.mp4"
    render_final(edited, ass_path, final, config, render_screen, voiceover)
    if args.final_dir:
        args.final_dir.mkdir(parents=True, exist_ok=True)
        final_copy = next_sequence_path(args.final_dir, args.final_prefix)
        shutil.copy2(final, final_copy)
        print(f"[done] upload copy: {final_copy}")
    print(f"[done] {final}")


if __name__ == "__main__":
    main()
