"""쇼츠 스타일 v2: 승인된 데모(SHORTS_REFERENCE_STYLE.md)의 자막·헤드카피·중앙 화면 값.

승인된 렌더러(aimax_video_pipeline.py)는 이미 만들어진 쇼츠의 renderer_sha256이 걸려 있어서
고치지 않는다. 그 렌더러가 쓴 ASS를 v2 값으로 바꾸고, 중앙 화면 필터를 한 군데 덧붙인다.
기대한 자리를 못 찾으면 조용히 넘어가지 않고 멈춘다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from content_production_policy import (
    HEADLINE_V2,
    SHORTS_STYLE_V2,
    SOURCE_SCREEN_V2,
    SUBTITLE_V2,
    WATERMARK_V2,
)

# 좌우 여백 40: 안전폭 1000px(content_production_policy.HEADLINE_SAFE_WIDTH_PX_V2)와 같은 기준이다.
STYLE_V2_MARGIN_LR = 40


class StyleV2Error(RuntimeError):
    pass


def _fax(value: float) -> str:
    return f"\\fax{value:g}"


def _title_style_line(title: dict[str, Any]) -> str:
    rgb = str(title.get("color", "29D6EA")).lstrip("#")
    # renderer.rgb_hex_to_ass와 같은 RGB -> &H00BBGGRR 변환이다.
    ass_color = f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()
    return (
        f"Style: Title,{title.get('font_name', 'BM HANNA 11yrs old')},{HEADLINE_V2['font_size']},"
        f"{ass_color},&H000000FF,&H00000000,&H00000000,"
        f"{HEADLINE_V2['bold']},{HEADLINE_V2['italic']},0,0,{HEADLINE_V2['scale_x']},100,0,0,1,"
        f"{HEADLINE_V2['outline']},{HEADLINE_V2['shadow']},5,"
        f"{STYLE_V2_MARGIN_LR},{STYLE_V2_MARGIN_LR},0,1"
    )


_TITLE_EVENT = re.compile(
    r"^Dialogue: (?P<layer>\d+),(?P<start>[^,]+),(?P<end>[^,]+),Title,,0,0,0,,"
    r"\{\\pos\((?P<x>\d+),(?P<y>\d+)\)\}(?P<text>.*)$"
)
_POS_EVENT = re.compile(
    r"^(?P<head>Dialogue: \d+,[^,]+,[^,]+,(?P<style>Default|Watermark),,0,0,0,,)"
    r"\{\\pos\((?P<x>\d+),(?P<y>\d+)\)\}(?P<text>.*)$"
)


def apply_style_v2_to_ass(ass_path: Path, config: dict[str, Any]) -> dict[str, int]:
    """승인 렌더러가 쓴 captions.ass를 v2로 바꾼다. 바꾼 이벤트 수를 돌려준다."""
    title = config.get("title") or {}
    lines = Path(ass_path).read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    counts = {"title_events": 0, "subtitle_events": 0, "watermark_events": 0, "title_style": 0}
    wrap_added = False
    for line in lines:
        if line.startswith("ScaledBorderAndShadow:") and not wrap_added:
            out.append(line)
            out.append("WrapStyle: 2")  # 소프트 줄바꿈 없음: 헤드카피는 항상 정확히 2줄
            wrap_added = True
            continue
        if line.startswith("Style: Title,"):
            out.append(_title_style_line(title))
            counts["title_style"] += 1
            continue
        match = _TITLE_EVENT.match(line)
        if match:
            rows = match.group("text").split(r"\N")
            if len(rows) != 2 or not all(rows):
                raise StyleV2Error("v2 헤드카피는 정확히 2줄이어야 합니다.")
            for row, y in zip(rows, (HEADLINE_V2["line_y1"], HEADLINE_V2["line_y2"])):
                out.append(
                    f"Dialogue: {match['layer']},{match['start']},{match['end']},Title,,0,0,0,,"
                    f"{{\\pos({HEADLINE_V2['x']},{y}){_fax(HEADLINE_V2['fax'])}}}{row}"
                )
            counts["title_events"] += 1
            continue
        match = _POS_EVENT.match(line)
        if match:
            subtitle = match["style"] == "Default"
            fax = SUBTITLE_V2["fax"] if subtitle else WATERMARK_V2["fax"]
            out.append(
                f"{match['head']}{{\\pos({match['x']},{match['y']}){_fax(fax)}}}{match['text']}"
            )
            counts["subtitle_events" if subtitle else "watermark_events"] += 1
            continue
        out.append(line)
    if not wrap_added or counts["title_style"] != 1 or counts["title_events"] != 1:
        raise StyleV2Error("승인 렌더러 ASS에서 v2로 바꿀 자리를 찾지 못했습니다.")
    if counts["subtitle_events"] < 1 or counts["watermark_events"] != 1:
        raise StyleV2Error("승인 렌더러 ASS의 자막·워터마크 이벤트를 찾지 못했습니다.")
    Path(ass_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return counts


def render_final_v2(
    renderer: Any,
    presenter: Path,
    ass: Path,
    output: Path,
    config: dict[str, Any],
    source: Path,
    voice_stem: Path,
) -> None:
    """renderer.render_final에 중앙 화면 밝기 필터(eq)를 한 번만 끼워 넣는다."""
    if config.get("layout") != "vertical_aimax":
        raise StyleV2Error("v2 중앙 화면 필터는 vertical_aimax 레이아웃에서만 끼울 수 있습니다.")
    screen_filter = str((config.get("screen") or {}).get("color_filter") or "")
    if screen_filter != SOURCE_SCREEN_V2["color_filter"]:
        raise StyleV2Error("render_config의 중앙 화면 필터가 v2 정본과 다릅니다.")
    original_run: Callable[..., Any] = renderer.run

    def run_with_screen_filter(cmd, **kwargs):
        cmd = list(cmd)
        if "-filter_complex" in cmd:
            index = cmd.index("-filter_complex") + 1
            graph = cmd[index]
            if graph.count("[screen];") != 1:
                raise StyleV2Error("중앙 화면 필터를 끼울 자리를 정확히 한 번 찾지 못했습니다.")
            cmd[index] = graph.replace("[screen];", f",{screen_filter}[screen];", 1)
        return original_run(cmd, **kwargs)

    renderer.run = run_with_screen_filter
    try:
        renderer.render_final(presenter, ass, output, config, source, voice_stem)
    finally:
        renderer.run = original_run


__all__ = ["SHORTS_STYLE_V2", "StyleV2Error", "apply_style_v2_to_ass", "render_final_v2"]
