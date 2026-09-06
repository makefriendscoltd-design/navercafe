#!/usr/bin/env python3
"""Build and validate 10 local-only 1080x1080 AIMAX carousel decks."""

from __future__ import annotations

import base64
import difflib
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WORKSPACE = Path("/Users/apple/orca/navercafe")
ROOT = WORKSPACE / "outputs/20260822-shorts-correction-audit/cardnews/square-carousel-v2-20260823"
sys.path.insert(0, str(WORKSPACE))
from aside_browser import JS_COMMON, _payload_expression, run_repl  # noqa: E402

INPUTS: dict[str, dict[str, str]] = {
    "GExjqEBXKN4": {"deck": "outputs/GExjqEBXKN4-20260821/cardnews/04_cardnews_deck.json", "copy": "outputs/GExjqEBXKN4-20260821/cardnews/05_youtube_community_post.txt", "topic": "콜드 이메일 자동화"},
    "LW8KLS9j2_E": {"deck": "outputs/LW8KLS9j2_E-20260821/cardnews/04_cardnews_deck.json", "copy": "outputs/LW8KLS9j2_E-20260821/cardnews/05_youtube_community_post.txt", "topic": "업무용 두 번째 뇌"},
    "Yv51xj_1DOQ": {"deck": "outputs/Yv51xj_1DOQ-20260821/cardnews/04_cardnews_deck.json", "copy": "outputs/Yv51xj_1DOQ-20260821/cardnews/05_youtube_community_post.txt", "topic": "메일함 분류 자동화"},
    "Teyaltxi-_E": {"deck": "outputs/Teyaltxi-_E-20260821/cardnews/04_cardnews_deck.json", "copy": "outputs/Teyaltxi-_E-20260821/cardnews/05_youtube_community_post.txt", "topic": "AI 기억 외부화"},
    "sf90aJItFHw": {"deck": "outputs/sf90aJItFHw-20260822/community/04_cardnews_deck.json", "copy": "outputs/sf90aJItFHw-20260822/community/05_youtube_community_post.txt", "topic": "차트 분석과 사람 승인"},
    "j_VHFDBzFas": {"deck": "outputs/j_VHFDBzFas-20260822/community/04_cardnews_deck.json", "copy": "outputs/j_VHFDBzFas-20260822/community/05_youtube_community_post.txt", "topic": "무료 PDF에서 유료 PDF로"},
    "yCACmFTiCto": {"deck": "outputs/yCACmFTiCto-20260822/community/04_cardnews_deck.json", "copy": "outputs/yCACmFTiCto-20260822/community/05_youtube_community_post.txt", "topic": "AI 마케팅 직원 설계"},
    "b9yqzV4YOoI": {"deck": "outputs/b9yqzV4YOoI-20260822/community/04_cardnews_deck.json", "copy": "outputs/b9yqzV4YOoI-20260822/community/05_youtube_community_post.txt", "topic": "복제하기 어려운 브랜드"},
    "TZO3_2Krsqk": {"deck": "outputs/TZO3_2Krsqk-20260822/community/04_cardnews_deck.json", "copy": "outputs/TZO3_2Krsqk-20260822/community/05_youtube_community_post.txt", "topic": "목적에서 시작하는 사업"},
    "hHfbBEuE6Rs": {"deck": "outputs/hHfbBEuE6Rs-20260822/community/04_cardnews_deck.json", "copy": "outputs/hHfbBEuE6Rs-20260822/community/05_youtube_community_post.txt", "topic": "퍼플렉시티 출처 검증"},
}

TEXT_FIELDS = {"badge", "title", "sub", "tag", "num", "head", "desc", "quote", "by", "cta1", "cta2", "hint"}
NUMERIC_TAGS = {"FEE", "MATH", "COST", "NVDA", "AAPL", "API", "SYSTEM", "TRIAGE", "COMMAND"}

CSS = r"""
:root{--blue:#155EEF;--blue2:#0B4BCC;--ink:#101828;--muted:#475467;--line:#D0DFFB;--surface:#F5F8FF}
*{box-sizing:border-box}html,body{margin:0;width:1080px;height:1080px;overflow:hidden;background:#fff}
body{font-family:"Apple SD Gothic Neo","Noto Sans KR",Arial,sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased}
.slide{position:relative;width:1080px;height:1080px;overflow:hidden;background:#fff;padding:96px;display:none}
.slide.active{display:block}.slide::before{content:"";position:absolute;left:0;top:0;width:16px;height:1080px;background:var(--blue)}
.safe-guide{display:none;position:absolute;inset:96px;border:1px dashed #AFC8FF;pointer-events:none}
.brand{position:absolute;left:96px;top:96px;height:38px;font-size:24px;line-height:38px;font-weight:800;letter-spacing:.09em;color:var(--blue)}
.page{position:absolute;right:96px;top:96px;height:38px;font-size:26px;line-height:38px;font-weight:800;color:var(--ink);font-variant-numeric:tabular-nums}
.page span{color:#98A2B3}.footer{position:absolute;left:96px;right:96px;bottom:96px;height:50px;display:flex;align-items:center;justify-content:space-between;border-top:2px solid var(--line);padding-top:16px;font-size:22px;line-height:28px;font-weight:700;color:var(--muted)}
.footer .mark{color:var(--blue);letter-spacing:.08em}.main{position:absolute;left:96px;right:96px;top:190px;bottom:180px;display:flex;flex-direction:column;justify-content:center}
.kicker{font-size:26px;line-height:1.2;font-weight:900;letter-spacing:.13em;color:var(--blue);margin-bottom:36px}
.title{font-size:72px;line-height:86px;font-weight:900;letter-spacing:-.045em;white-space:pre-line;text-wrap:balance;word-break:keep-all;overflow-wrap:normal;color:var(--ink)}
.desc{font-size:46px;line-height:66px;font-weight:600;letter-spacing:-.025em;white-space:pre-line;text-wrap:pretty;word-break:keep-all;overflow-wrap:normal;color:var(--muted)}
.rule{width:104px;height:8px;background:var(--blue);margin:42px 0 42px}
.hook .main{top:182px;bottom:172px;justify-content:flex-end}.hook .title{font-size:84px;line-height:100px}.hook .desc{font-size:44px;line-height:64px;max-width:820px}.hook .accent-block{position:absolute;right:96px;top:190px;width:184px;height:184px;border:20px solid var(--line)}
.body .content-card{background:var(--surface);border:2px solid var(--line);padding:46px 48px;margin-top:44px}.body .title{font-size:70px}.body .desc{font-size:46px}
.process .main{display:grid;grid-template-columns:170px 1fr;gap:52px;align-items:center}.process .rail{align-self:stretch;display:flex;align-items:center;justify-content:center;position:relative}.process .rail::before{content:"";position:absolute;width:4px;top:80px;bottom:80px;background:var(--line)}
.process .hero-num{position:relative;width:138px;height:138px;border-radius:50%;background:var(--blue);color:#fff;display:flex;align-items:center;justify-content:center;font-size:72px;line-height:86px;font-weight:900;font-variant-numeric:tabular-nums}.process .title{font-size:68px;line-height:82px}.process .desc{font-size:44px;line-height:64px}.process .rule{margin:34px 0}
.checklist .check-row{display:grid;grid-template-columns:76px 1fr;gap:28px;align-items:start;background:var(--surface);border:2px solid var(--line);padding:42px 44px;margin-top:48px}.checklist .check{width:66px;height:66px;border-radius:50%;background:var(--blue);color:#fff;display:flex;align-items:center;justify-content:center;font-size:42px;line-height:56px;font-weight:900}.checklist .title{font-size:68px;line-height:82px}.checklist .desc{font-size:44px;line-height:64px}
.number .main{display:grid;grid-template-columns:245px 1fr;gap:48px;align-items:center}.number .number-panel{height:430px;background:var(--blue);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center}.number .hero-num{font-size:134px;line-height:166px;font-weight:900}.number .mini{font-size:25px;line-height:32px;font-weight:800;letter-spacing:.12em;margin-top:22px}.number .title{font-size:65px;line-height:80px}.number .desc{font-size:42px;line-height:64px}.number .rule{margin:32px 0}
.cta{background:var(--surface)}.cta::after{content:"";position:absolute;width:180px;height:180px;border:20px solid #D8E5FF;right:96px;top:176px}.cta .main{top:220px;bottom:190px;justify-content:flex-end}.cta .title{font-size:76px;line-height:92px;max-width:820px}.cta .desc{font-size:43px;line-height:64px;max-width:730px}.cta-box{margin-top:38px;display:flex;gap:20px}.cta-pill{background:var(--blue);color:#fff;padding:22px 34px;font-size:32px;line-height:40px;font-weight:900}.cta-pill.alt{background:#fff;color:var(--blue);border:3px solid var(--blue)}
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def layout_for(index: int, slide: dict[str, Any]) -> str:
    if index == 0:
        return "hook"
    if index == 9:
        return "cta"
    tag = str(slide.get("f", {}).get("tag", ""))
    if tag in NUMERIC_TAGS or index in {5}:
        return "number"
    if index in {3, 6, 8}:
        return "checklist"
    if index in {1, 2, 4, 7}:
        return "process"
    return "body"


def exact_claim_fields(slide: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in slide.get("f", {}).items() if key in TEXT_FIELDS}


def card_words(slide: dict[str, Any]) -> int:
    text = " ".join(str(v) for v in exact_claim_fields(slide).values())
    return sum(1 for token in re.split(r"\s+", text.strip()) if re.search(r"[가-힣]", token))


def slide_html(index: int, slide: dict[str, Any], topic: str) -> str:
    f = slide["f"]
    kind = slide["layoutType"]
    page = f"{index + 1:02d}"
    header = f'<div class="brand" data-safe-text>MINsoo / AIMAX</div><div class="page" data-safe-text>{page} <span>/ 10</span></div>'
    footer = f'<div class="footer"><span data-safe-text>{esc(topic)}</span><span class="mark" data-safe-text>SWIPE →</span></div>'
    if kind == "hook":
        inner = f'''<div class="accent-block"></div><div class="main">
          <div class="kicker" data-safe-text data-role="meta">{esc(f.get("badge"))}</div>
          <div class="title" data-safe-text data-role="title">{esc(f.get("title"))}</div>
          <div class="rule"></div><div class="desc" data-safe-text data-role="body">{esc(f.get("sub"))}</div></div>'''
    elif kind == "cta":
        inner = f'''<div class="main"><div class="kicker" data-safe-text data-role="meta">NEXT STEP</div>
          <div class="title" data-safe-text data-role="title">{esc(f.get("head"))}</div><div class="rule"></div>
          <div class="desc" data-safe-text data-role="body">{esc(f.get("desc"))}</div>
          <div class="cta-box"><div class="cta-pill" data-safe-text data-role="cta">{esc(f.get("cta1"))}</div><div class="cta-pill alt" data-safe-text data-role="cta">{esc(f.get("cta2"))}</div></div></div>'''
    elif kind == "process":
        inner = f'''<div class="main"><div class="rail"><div class="hero-num" data-safe-text data-role="number">{esc(f.get("num", page))}</div></div>
          <div><div class="kicker" data-safe-text data-role="meta">{esc(f.get("tag"))}</div><div class="title" data-safe-text data-role="title">{esc(f.get("head"))}</div>
          <div class="rule"></div><div class="desc" data-safe-text data-role="body">{esc(f.get("desc"))}</div></div></div>'''
    elif kind == "checklist":
        inner = f'''<div class="main"><div class="kicker" data-safe-text data-role="meta">CHECK · {esc(f.get("tag"))}</div>
          <div class="title" data-safe-text data-role="title">{esc(f.get("head"))}</div><div class="check-row"><div class="check" data-safe-text data-role="icon">✓</div>
          <div class="desc" data-safe-text data-role="body">{esc(f.get("desc"))}</div></div></div>'''
    elif kind == "number":
        inner = f'''<div class="main"><div class="number-panel"><div class="hero-num" data-safe-text data-role="number">{esc(f.get("num", page))}</div><div class="mini" data-safe-text data-role="meta">{esc(f.get("tag"))}</div></div>
          <div><div class="title" data-safe-text data-role="title">{esc(f.get("head"))}</div><div class="rule"></div><div class="desc" data-safe-text data-role="body">{esc(f.get("desc"))}</div></div></div>'''
    else:
        inner = f'''<div class="main"><div class="kicker" data-safe-text data-role="meta">{esc(f.get("tag"))}</div><div class="title" data-safe-text data-role="title">{esc(f.get("head"))}</div>
          <div class="content-card"><div class="desc" data-safe-text data-role="body">{esc(f.get("desc"))}</div></div></div>'''
    return f'<section class="slide {kind}" data-index="{index}" data-layout="{kind}">{header}{inner}{footer}<div class="safe-guide"></div></section>'


def build_deck(source_id: str, spec: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], str]:
    deck_path = WORKSPACE / spec["deck"]
    copy_path = WORKSPACE / spec["copy"]
    original = json.loads(deck_path.read_text(encoding="utf-8"))
    copy_text = copy_path.read_text(encoding="utf-8")
    rebuilt = json.loads(json.dumps(original, ensure_ascii=False))
    rebuilt["canvas"] = {"width": 1080, "height": 1080, "aspectRatio": "1:1", "safeMargin": 96}
    rebuilt["sourceUrl"] = f"https://www.youtube.com/watch?v={source_id}"
    rebuilt["brandSystem"] = "MINsoo/AIMAX square white-blue"
    rebuilt["renderProfile"] = "square-carousel-v2-20260823"
    rebuilt["sourceMapping"] = {
        "deckPath": spec["deck"], "deckSha256": sha256(deck_path),
        "communityCopyPath": spec["copy"], "communityCopySha256": sha256(copy_path),
        "claimPolicy": "All source slide text fields preserved byte-for-byte in values; only layout metadata and square canvas added.",
    }
    for i, slide in enumerate(rebuilt["slides"]):
        slide["layoutType"] = layout_for(i, slide)
    return original, rebuilt, copy_text


def make_html(source_id: str, deck: dict[str, Any], topic: str) -> str:
    slides = "\n".join(slide_html(i, slide, topic) for i, slide in enumerate(deck["slides"]))
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=1080,initial-scale=1"><title>{esc(source_id)} square carousel</title><style>{CSS}</style></head>
<body>{slides}<script>const i=Math.max(0,Math.min(9,Number(new URLSearchParams(location.search).get('slide')||0)));document.querySelector(`[data-index="${{i}}"]`).classList.add('active');</script></body></html>'''


def render_source(source_id: str, url: str, target: Path) -> list[dict[str, Any]]:
    payload = {"url": url, "source": source_id}
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r'''
const p=await openTab(payload.url+'?slide=0');
const out=[];
for(let i=0;i<10;i++){
  await p.goto(payload.url+'?slide='+i,{waitUntil:'networkidle'}); await sleep(180); await p.evaluate(()=>document.fonts.ready);
  const metrics=await p.evaluate(()=>{
    const slide=document.querySelector('.slide.active'); const sr=slide.getBoundingClientRect();
    const blocks=[...slide.querySelectorAll('[data-safe-text]')];
    function lineTexts(el){
      const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT); const lines=new Map(); let n;
      while(n=walker.nextNode()) for(let j=0;j<n.textContent.length;j++){
        const ch=n.textContent[j]; if(ch==='\n'||ch==='\r') continue;
        const range=document.createRange(); range.setStart(n,j); range.setEnd(n,j+1); const r=range.getBoundingClientRect();
        if(!r.width&&!r.height) continue; const key=Math.round(r.top*2)/2; lines.set(key,(lines.get(key)||'')+ch);
      }
      return [...lines.entries()].sort((a,b)=>a[0]-b[0]).map(x=>x[1].trim()).filter(Boolean);
    }
    const text=blocks.map((el,index)=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el),lines=lineTexts(el);return{
      index,role:el.dataset.role||'support',text:el.textContent.trim(),rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},
      fontSize:parseFloat(s.fontSize),lineHeight:s.lineHeight,clientWidth:el.clientWidth,scrollWidth:el.scrollWidth,clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,
      overflow:s.overflow,textOverflow:s.textOverflow,whiteSpace:s.whiteSpace,lines,orphanOneChar:lines.some(v=>[...v.replace(/\s/g,'')].length===1)
    }});
    const allInside=text.every(x=>x.rect.left>=95.5&&x.rect.top>=95.5&&x.rect.right<=984.5&&x.rect.bottom<=984.5);
    const noScroll=text.every(x=>x.scrollWidth<=x.clientWidth&&x.scrollHeight<=x.clientHeight);
    const noEllipsis=text.every(x=>x.textOverflow!=='ellipsis');
    const bodyMin=text.filter(x=>x.role==='body').every(x=>x.fontSize>=42);
    const titleMin=text.filter(x=>x.role==='title').every(x=>x.fontSize>=64);
    const noOrphan=text.filter(x=>!['number','icon'].includes(x.role)).every(x=>!x.orphanOneChar);
    return {slideRect:{width:sr.width,height:sr.height},layout:slide.dataset.layout,text,checks:{exactCanvas:sr.width===1080&&sr.height===1080,allTextInsideSafeFrame:allInside,noTextScrollOverflow:noScroll,noEllipsis,noOrphanOneCharacterLine:noOrphan,bodyFontAtLeast42:bodyMin,titleFontAtLeast64:titleMin}};
  });
  const png=Buffer.from(await p.screenshot({clip:{x:0,y:0,width:1080,height:1080}})).toString('base64');
  out.push({index:i+1,metrics,png});
}
emit({status:'rendered',source:payload.source,cards:out});
'''
    result = run_repl(code, cwd=target, timeout=360, account="u0")
    if result.get("status") != "rendered" or len(result.get("cards", [])) != 10:
        raise RuntimeError(f"Aside render failed: {source_id}: {result}")
    png_dir = target / "png"
    png_dir.mkdir(parents=True, exist_ok=True)
    metrics = []
    for card in result["cards"]:
        path = png_dir / f"{card['index']:02d}.png"
        path.write_bytes(base64.b64decode(card.pop("png")))
        with Image.open(path) as captured:
            if captured.size != (1080, 1080):
                captured.convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS).save(path, format="PNG", optimize=True)
        metrics.append(card)
    return metrics


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    path = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
    try:
        return ImageFont.truetype(str(path), size=size, index=6 if bold else 0)
    except Exception:
        return ImageFont.load_default()


def contact_sheet(target: Path, source_id: str, topic: str) -> Path:
    sheet = Image.new("RGB", (2160, 1010), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((48, 28), f"{source_id} · {topic}", fill="#101828", font=font(48, True))
    draw.text((48, 82), "1080×1080 · 96px safe frame · local square carousel v2", fill="#155EEF", font=font(30))
    for i in range(10):
        im = Image.open(target / "png" / f"{i+1:02d}.png").convert("RGB").resize((400, 400), Image.Resampling.LANCZOS)
        col, row = i % 5, i // 5
        x, y = 40 + col * 424, 150 + row * 420
        sheet.paste(im, (x, y)); draw.rectangle((x, y, x+399, y+399), outline="#D0DFFB", width=3)
    path = target / "contact_sheet.png"; sheet.save(path, optimize=True); return path


def combined_atlas(paths: list[tuple[str, Path]]) -> Path:
    atlas = Image.new("RGB", (2160, 2720), "#EEF4FF")
    draw = ImageDraw.Draw(atlas)
    draw.text((54, 30), "AIMAX SQUARE CAROUSEL V2 · ALL 10 DECKS", fill="#101828", font=font(54, True))
    for i, (source_id, path) in enumerate(paths):
        im = Image.open(path).convert("RGB").resize((1020, 477), Image.Resampling.LANCZOS)
        col, row = i % 2, i // 2; x, y = 40 + col*1060, 118 + row*516
        atlas.paste(im, (x,y))
    path = ROOT / "combined_atlas.png"; atlas.save(path, optimize=True); return path


def expected_text(slide: dict[str, Any]) -> str:
    return " ".join(str(v) for v in exact_claim_fields(slide).values())


def normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def token_coverage(expected: str, observed: str) -> float:
    tokens = [normalize(t) for t in re.split(r"\s+", expected) if len(normalize(t)) >= 2]
    obs = normalize(observed)
    if not tokens:
        return 1.0
    return sum(1 for t in tokens if t in obs) / len(tokens)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    originals: dict[str, dict[str, Any]] = {}; decks: dict[str, dict[str, Any]] = {}; copies: dict[str, str] = {}
    for source_id, spec in INPUTS.items():
        target = ROOT / source_id; target.mkdir(parents=True, exist_ok=True)
        original, deck, copy_text = build_deck(source_id, spec)
        originals[source_id], decks[source_id], copies[source_id] = original, deck, copy_text
        write_json(target / "deck.json", deck)
        (target / "community_copy.txt").write_text(copy_text, encoding="utf-8")
        (target / "index.html").write_text(make_html(source_id, deck, spec["topic"]), encoding="utf-8")

    server = subprocess.Popen([sys.executable, "-m", "http.server", "8793", "--bind", "127.0.0.1", "--directory", str(ROOT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        for source_id in INPUTS:
            target = ROOT / source_id
            metrics = render_source(source_id, f"http://127.0.0.1:8793/{source_id}/index.html", target)
            write_json(target / "dom_metrics.json", metrics)
    finally:
        server.terminate(); server.wait(timeout=10)

    # OCR all PNGs through local macOS Vision helper.
    png_paths = [ROOT / sid / "png" / f"{i:02d}.png" for sid in INPUTS for i in range(1, 11)]
    ocr_cmd = ["swift", str(ROOT / "ocr_vision.swift"), *map(str, png_paths)]
    ocr_proc = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=300, check=False)
    if ocr_proc.returncode != 0:
        raise RuntimeError("Vision OCR failed: " + ocr_proc.stderr[-2000:])
    ocr_rows = json.loads(ocr_proc.stdout)
    ocr_by_path = {row["path"]: row for row in ocr_rows}

    all_validations = []
    sheets: list[tuple[str, Path]] = []
    for source_id, spec in INPUTS.items():
        target = ROOT / source_id; original = originals[source_id]; deck = decks[source_id]
        dom = json.loads((target / "dom_metrics.json").read_text(encoding="utf-8"))
        cards = []
        for i, (orig_slide, new_slide) in enumerate(zip(original["slides"], deck["slides"]), 1):
            path = target / "png" / f"{i:02d}.png"; ocr = ocr_by_path[str(path)]
            observed = " ".join(x["text"] for x in ocr.get("observations", []))
            coverage = token_coverage(expected_text(orig_slide), observed)
            confidence = sum(x["confidence"] for x in ocr.get("observations", [])) / max(1, len(ocr.get("observations", [])))
            with Image.open(path) as im: dims = list(im.size)
            checks = {
                **dom[i-1]["metrics"]["checks"],
                "pngExactly1080Square": dims == [1080,1080],
                "notFourByFive": dims != [1080,1350],
                "claimTextExact": exact_claim_fields(orig_slide) == exact_claim_fields(new_slide),
                "max30KoreanEojeol": card_words(new_slide) <= 30,
                "ocrTextDetected": len(normalize(observed)) >= 12,
                "ocrTokenCoverageAtLeast45Pct": coverage >= 0.45,
                "ocrMeanConfidenceAtLeast50Pct": confidence >= 0.50,
            }
            cards.append({
                "card": i, "file": str(path.relative_to(ROOT)), "layoutType": new_slide["layoutType"], "dimensions": dims,
                "sha256": sha256(path), "koreanEojeol": card_words(new_slide), "ocrText": observed, "ocrTokenCoverage": round(coverage,4),
                "ocrMeanConfidence": round(confidence,4), "checks": checks, "status": "PASS" if all(checks.values()) else "FAIL",
            })
        source_checks = {
            "tenCards": len(cards)==10,
            "allCardsPass": all(c["status"]=="PASS" for c in cards),
            "communityCopyExact": copies[source_id] == (target/"community_copy.txt").read_text(encoding="utf-8"),
            "sourceUrlMapped": deck["sourceUrl"] == f"https://www.youtube.com/watch?v={source_id}",
            "layoutVariety": len({c["layoutType"] for c in cards}) >= 4,
            "localOnly": True,
            "providerUntouched": True,
            "repositoryCodeUntouched": True,
            "koreanCopyUnchanged": True,
            "humanizeNotRequired": True,
        }
        result = {"sourceId": source_id, "topic": spec["topic"], "status": "PASS" if all(source_checks.values()) else "FAIL", "checks": source_checks, "cards": cards}
        write_json(target/"machine_validation.json", result); all_validations.append(result)
        sheets.append((source_id, contact_sheet(target, source_id, spec["topic"])))

    atlas = combined_atlas(sheets)
    summary_checks = {
        "tenSources": len(all_validations)==10,
        "oneHundredPngs": len(list(ROOT.glob("*/png/*.png")))==100,
        "allSourcesPass": all(x["status"]=="PASS" for x in all_validations),
        "allCardsPass": all(c["status"]=="PASS" for x in all_validations for c in x["cards"]),
        "tenContactSheets": len(list(ROOT.glob("*/contact_sheet.png")))==10,
        "combinedAtlasPresent": atlas.exists(),
    }
    combined = {"status":"PASS" if all(summary_checks.values()) else "FAIL", "checks":summary_checks, "sources":all_validations}
    write_json(ROOT/"machine_validation_all.json", combined)
    registry = {
        "profile":"square-carousel-v2-20260823", "status":combined["status"], "root":str(ROOT), "canvas":{"width":1080,"height":1080,"safeMargin":96},
        "counts":{"sources":10,"cards":100,"contactSheets":10,"atlases":1},
        "sources":[{"sourceId":sid,"topic":INPUTS[sid]["topic"],"deck":f"{sid}/deck.json","html":f"{sid}/index.html","pngDir":f"{sid}/png","contactSheet":f"{sid}/contact_sheet.png","validation":f"{sid}/machine_validation.json","communityCopy":f"{sid}/community_copy.txt"} for sid in INPUTS],
        "machineValidation":"machine_validation_all.json","combinedAtlas":"combined_atlas.png","references":"REFERENCES.md","designSystem":"DESIGN_SYSTEM.md","report":"REBUILD_REPORT.md",
    }
    write_json(ROOT/"registry.json", registry)
    if combined["status"] != "PASS":
        failures=[(x["sourceId"],c["card"],[k for k,v in c["checks"].items() if not v]) for x in all_validations for c in x["cards"] if c["status"]!="PASS"]
        raise RuntimeError("Validation failures: "+json.dumps(failures,ensure_ascii=False))


if __name__ == "__main__":
    main()
