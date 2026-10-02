"""쇼츠 스타일 v2(112px 헤드카피, 82px 자막) 프로필과 v1 하위호환."""
import copy
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

import content_production_policy as policy
import shorts_style_v2

DEMO_LINES = ("월 1800만 원 벌었다는", "AI 광고 에이전시 5단계")
RENDERER = (
    Path(__file__).resolve().parent
    / "outputs/uX6zwf4b8sM-20260829/shorts/renderer/aimax_video_pipeline.py"
)


def _render_payload(style=None):
    presenter_name, presenter_hash = next(iter(policy.MINSOO_PRESENTER_ASSETS.items()))
    v2 = style == policy.SHORTS_STYLE_V2
    profile = policy.shorts_style_profile(style)
    title = dict(profile["title"])
    if v2:
        title["text"] = "\n".join(DEMO_LINES)
    payload = {
        "output_width": policy.VIDEO["width"],
        "output_height": policy.VIDEO["height"],
        "fps": policy.VIDEO["fps"],
        "title": title,
        "subtitle": dict(profile["subtitle"]),
        "presenter": policy.PRESENTER,
        "screen": dict(profile["screen"]),
        "watermark": dict(profile["watermark"]),
        "render_provenance": {
            "source_and_presenter_audio_mapped": False,
            "source_audio_mapped": False,
            "presenter_audio_mapped": False,
            "presenter_input": presenter_name,
            "presenter_sha256": presenter_hash,
            "source_footage": "source.mp4",
            "source_footage_sha256": "source-sha256",
            "v7_reference_restoration": {
                "voice_settings": policy.MINSOO_VOICE_SETTINGS,
                "headline_font_size_1080": profile["headline_font_size_1080"],
                "subtitle_rule": policy.SUBTITLE["rule"],
                **({"style_version": style} if v2 else {}),
            },
        },
    }
    if v2:
        payload["style_version"] = style
    return payload


def test_v2_profile_constants_are_the_approved_demo_values():
    assert policy.SHORTS_STYLE_ACTIVE == policy.SHORTS_STYLE_V2
    head = policy.HEADLINE_V2
    assert (head["font_size"], head["scale_x"], head["bold"], head["outline"], head["shadow"]) == (112, 92, 0, 3, 3)
    assert head["fax"] == -0.18 and (head["line_y1"], head["line_y2"]) == (385, 513)
    sub = policy.SUBTITLE_V2
    assert (sub["font_size"], sub["bold"], sub["italic"], sub["outline"], sub["shadow"]) == (82, -1, 0, 4, 3)
    assert (sub["x"], sub["y"], sub["fax"]) == (540, 940, -0.16)
    assert policy.WATERMARK_V2["fax"] == -0.18
    # v1은 바뀌지 않는다.
    assert policy.HEADLINE["font_size"] == 90 and policy.SUBTITLE["font_size"] == 59
    assert policy.HEADLINE_SAFE_WIDTH_PX == 920 and policy.HEADLINE_SAFE_PROXY_CHAR_LIMIT == 13


def test_v2_width_gate_passes_the_approved_demo_lines():
    evidence = policy.validate_headline_pixel_width(DEMO_LINES, policy.SHORTS_STYLE_V2)
    assert evidence["style_version"] == "v2" and evidence["horizontal_scale"] == pytest.approx(0.92)
    assert max(evidence["line_widths_px"]) <= policy.HEADLINE_SAFE_WIDTH_PX_V2
    # 실측: 935px, 959px(90px 기준으로는 817px, 837px라 v1 안전폭 920px도 통과한다).
    assert evidence["line_widths_px"] == pytest.approx([935.4, 959.0], abs=1.0)
    assert max(policy.validate_headline_pixel_width(DEMO_LINES)["line_widths_px"]) <= policy.HEADLINE_SAFE_WIDTH_PX


def test_v2_width_gate_rejects_clearly_too_wide_lines():
    wide = ("월 1800만 원 벌었다는 사람이", "AI 광고 에이전시 5단계")
    with pytest.raises(policy.ProductionPolicyError, match="112px 안전폭 1000px"):
        policy.validate_headline_pixel_width(wide, policy.SHORTS_STYLE_V2)
    # 18자 한도 안이어도 한글만 18자면 v2 폭을 넘는다.
    with pytest.raises(policy.ProductionPolicyError, match="112px 안전폭 1000px"):
        policy.validate_headline_pixel_width(("가나다라마바사아자차카타파하", "가나다라"), policy.SHORTS_STYLE_V2)


def test_v2_width_is_stricter_than_v1_for_the_same_line():
    # 같은 줄에서 v2 폭은 v1의 112 x 0.92 / 90 = 1.1449배다.
    v1 = policy.validate_headline_pixel_width(("가나다라마바사아", "가나다라마바사"))["line_widths_px"]
    v2 = policy.validate_headline_pixel_width(
        ("가나다라마바사아", "가나다라마바사"), policy.SHORTS_STYLE_V2)["line_widths_px"]
    assert [b / a for a, b in zip(v1, v2)] == pytest.approx([112 * 0.92 / 90] * 2, rel=0.03)


def test_v2_proxy_char_limit_comes_from_the_widest_hangul_syllable():
    from PIL import ImageFont

    font = ImageFont.truetype(str(policy.SHORTS_TITLE_FONT_PATH), policy.HEADLINE_V2["font_size"])
    widest = max(font.getlength(chr(code)) for code in range(0xAC00, 0xD7A4)) * policy.HEADLINE_V2_HORIZONTAL_SCALE
    limit = policy.HEADLINE_SAFE_PROXY_CHAR_LIMIT_V2
    assert limit * widest <= policy.HEADLINE_SAFE_WIDTH_PX_V2 < (limit + 1) * widest


def test_validate_head_copy_uses_v2_width_only_when_asked():
    import notebooklm_shorts as scripts

    line = "월 1800만 원 번 AI 사람\nAI 광고 에이전시 5단계"
    # v1 폭(90px 883px, 920px 이하)은 통과하지만 v2 폭(1011px)은 넘는 줄이다.
    assert scripts.validate_head_copy(line) == line
    with pytest.raises(RuntimeError, match="112px"):
        scripts.validate_head_copy(line, style_version=policy.SHORTS_STYLE_V2)
    assert scripts.validate_head_copy("\n".join(DEMO_LINES), style_version=policy.SHORTS_STYLE_V2)


def test_old_render_config_without_style_version_still_validates_against_v1():
    payload = _render_payload()
    assert "style_version" not in payload
    policy.validate_render_evidence(payload)  # 90px, 59px
    payload["style_version"] = "v1"
    policy.validate_render_evidence(payload)


def test_v2_render_config_validates_and_v1_numbers_do_not_pass_as_v2():
    policy.validate_render_evidence(_render_payload(policy.SHORTS_STYLE_V2))
    mixed = _render_payload()
    mixed["style_version"] = "v2"  # v1 숫자에 이름만 v2
    with pytest.raises(policy.ProductionPolicyError):
        policy.validate_render_evidence(mixed)
    wrong_size = _render_payload(policy.SHORTS_STYLE_V2)
    wrong_size["subtitle"]["font_size"] = 59
    with pytest.raises(policy.ProductionPolicyError, match="subtitle.font_size"):
        policy.validate_render_evidence(wrong_size)
    unknown = _render_payload()
    unknown["style_version"] = "v3"
    with pytest.raises(policy.ProductionPolicyError, match="알 수 없는"):
        policy.validate_render_evidence(unknown)


def test_v2_render_config_remeasures_the_title_text():
    payload = _render_payload(policy.SHORTS_STYLE_V2)
    payload["title"]["text"] = "월 1800만 원 벌었다는 사람이\nAI 광고 에이전시 5단계"
    with pytest.raises(policy.ProductionPolicyError, match="안전폭"):
        policy.validate_render_evidence(payload)
    payload["title"]["text"] = ""
    with pytest.raises(policy.ProductionPolicyError, match="2줄"):
        policy.validate_render_evidence(payload)
    missing = _render_payload(policy.SHORTS_STYLE_V2)
    del missing["render_provenance"]["v7_reference_restoration"]["style_version"]
    with pytest.raises(policy.ProductionPolicyError, match="스타일 v2 증거"):
        policy.validate_render_evidence(missing)


def _real_renderer():
    if not RENDERER.is_file():
        pytest.skip("approved renderer not present")
    spec = importlib.util.spec_from_file_location("approved_v7_renderer_test", RENDERER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _v2_config():
    import json
    cfg = {
        "output_width": 1080, "output_height": 1920, "fps": 30, "layout": "vertical_aimax",
        "subtitle": {
            "font_name": "Cafe24 Ohsquare", "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
            "back_color": "&H00000000", "margin_v": 0, "alignment": 5, "max_chars_per_line": 30,
            "mode": "eojel", "merge_enabled": False,
            **{k: v for k, v in policy.SUBTITLE_V2.items() if k != "rule"},
        },
        "title": {"font_name": "BM HANNA 11yrs old", "color": "29D6EA", "text": "\n".join(DEMO_LINES),
                  **policy.HEADLINE_V2},
        "watermark": {"font_name": "BM HANNA 11yrs old", "color": "29D6EA",
                      **policy.WATERMARK_V2},
        "source": {"text": ""},
        "screen": {"color_filter": policy.SOURCE_SCREEN_V2["color_filter"]},
    }
    json.dumps(cfg)
    return cfg


def test_v2_ass_has_sizes_shear_and_two_fixed_headline_lines(tmp_path):
    renderer = _real_renderer()
    cfg = _v2_config()
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,500\n이\n\n2\n00:00:00,500 --> 00:00:01,000\n남자\n",
                   encoding="utf-8")
    ass = tmp_path / "c.ass"
    renderer.srt_to_ass(srt, ass, cfg)
    counts = shorts_style_v2.apply_style_v2_to_ass(ass, cfg)
    text = ass.read_text(encoding="utf-8")
    assert counts == {"title_events": 1, "subtitle_events": 2, "watermark_events": 1, "title_style": 1}
    assert "Style: Default,Cafe24 Ohsquare,82," in text
    assert "Style: Title,BM HANNA 11yrs old,112,&H00EAD629," in text
    assert ",0,0,0,0,92,100,0,0,1,3,3,5,40,40,0,1" in text  # Bold 끔, Italic 끔, ScaleX 92, 외곽선 3, 그림자 3
    assert text.count("\\fax-0.18") == 3  # 헤드카피 두 줄 + 워터마크
    assert text.count("\\fax-0.16") == 2  # 자막 한 어절마다
    assert "{\\pos(540,385)\\fax-0.18}월 1800만 원 벌었다는" in text
    assert "{\\pos(540,513)\\fax-0.18}AI 광고 에이전시 5단계" in text
    assert "{\\pos(540,940)\\fax-0.16}이" in text
    assert "{\\pos(540,1768)\\fax-0.18}@aimax" in text
    policy.validate_style_v2_ass(ass)


def test_v2_ass_conversion_fails_closed_on_a_one_line_headline(tmp_path):
    renderer = _real_renderer()
    cfg = _v2_config()
    cfg["title"]["text"] = "한 줄뿐"
    srt = tmp_path / "c.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,500\n이\n", encoding="utf-8")
    ass = tmp_path / "c.ass"
    renderer.srt_to_ass(srt, ass, cfg)
    with pytest.raises(shorts_style_v2.StyleV2Error):
        shorts_style_v2.apply_style_v2_to_ass(ass, cfg)


def test_v1_ass_is_rejected_by_the_v2_ass_validator(tmp_path):
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\nStyle: Title,BM HANNA 11yrs old,90,\n", encoding="utf-8")
    with pytest.raises(policy.ProductionPolicyError, match="정본 값이 없습니다"):
        policy.validate_style_v2_ass(ass)
    with pytest.raises(policy.ProductionPolicyError, match="없습니다"):
        policy.validate_style_v2_ass(tmp_path / "missing.ass")


def test_screen_filter_is_injected_exactly_once_without_touching_the_renderer():
    class FakeRenderer:
        seen = []

        def run(self, cmd, **kwargs):
            self.seen.append(list(cmd))

        def render_final(self, *args):
            self.run(["ffmpeg", "-filter_complex",
                      "[1:v]scale=1080:608,setsar=1,setpts=PTS/2[screen];[base][screen]overlay=0:664[v1]"])

    fake = FakeRenderer()
    original_run = fake.run
    shorts_style_v2.render_final_v2(fake, Path("p"), Path("a"), Path("o"), _v2_config(), Path("s"), Path("v"))
    graph = fake.seen[0][2]
    assert graph.count("eq=gamma=1.35:brightness=0.03:saturation=1.15") == 1
    assert "setpts=PTS/2,eq=gamma=1.35:brightness=0.03:saturation=1.15[screen];[base][screen]overlay" in graph
    assert fake.run.__func__ is original_run.__func__  # 끝나면 원래 run으로 돌아간다
    wrong = _v2_config()
    wrong["screen"]["color_filter"] = "eq=gamma=2"
    with pytest.raises(shorts_style_v2.StyleV2Error):
        shorts_style_v2.render_final_v2(fake, Path("p"), Path("a"), Path("o"), wrong, Path("s"), Path("v"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_v2_mix_keeps_every_audio_gate_while_raising_bgm_and_sfx(tmp_path, monkeypatch):
    import shorts_v7_builder as builder

    if not builder.BGM.is_file():
        pytest.skip("locked BGM asset not present")
    dur = 12.0
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    voice_raw = tmp_path / "voice_raw.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", f"anoisesrc=d={dur}:c=pink:r=48000:a=0.5",
        "-af", "tremolo=f=4:d=0.9,lowpass=f=3500,highpass=f=120", "-ac", "2", voice_raw], check=True)
    voice = tmp_path / "voice_stem.wav"
    builder.normalize_loudness(voice_raw, voice, -14.8, -1.8)
    builder.calibrate_voice_lufs(voice)
    sfx_raw = builder.build_sfx_stem([1.0, 3.0, 5.0, 7.0, 9.0], dur)
    profile = builder.build_mix_v2(
        voice, sfx_raw, dur, tmp_path / "bgm_stem.wav", tmp_path / "sfx_stem.wav", tmp_path / "pre.wav")
    v = builder.loudness(voice)
    b = builder.loudness(tmp_path / "bgm_stem.wav")
    s = builder.loudness(tmp_path / "sfx_stem.wav")
    gates = policy.AUDIO_GATES
    assert v["integrated_lufs"] - b["integrated_lufs"] >= gates["voice_minus_bgm_min_lu"]
    assert v["true_peak_dbtp"] - s["true_peak_dbtp"] >= gates["voice_peak_minus_sfx_peak_min_db"]
    assert profile["stem_gain_vs_asset"]["bgm"] <= profile["gate_caps_stem_gain"]["bgm"] + 1e-9
    assert profile["stem_gain_vs_asset"]["sfx"] <= profile["gate_caps_stem_gain"]["sfx"] + 1e-9
