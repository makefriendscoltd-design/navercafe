#!/usr/bin/env python3
"""Render and validate this local-only 1080x1080 Community card-news deck."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
SOURCE_ID = "4hKJ9X6rGFo"
TOPIC = "4C로 역할별 AI 팀을 설계하는 법"
POLICY_ROOT = Path(__file__).resolve().parent
BUILDER_PATH = Path(
    "/Users/apple/orca/navercafe/outputs/20260822-shorts-correction-audit/"
    "cardnews/square-carousel-v2-20260823/build_square_carousel.py"
)
OCR_PATH = BUILDER_PATH.parent / "ocr_vision.swift"
PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")
sys.path.insert(0, str(POLICY_ROOT))
from content_production_policy import validate_cardnews_editorial_deck  # noqa: E402


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def load_builder():
    spec = importlib.util.spec_from_file_location("approved_square_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("승인된 정사각형 카드뉴스 빌더를 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render() -> None:
    builder = load_builder()
    deck_path = ROOT / "04_cardnews_deck.json"
    deck = json.loads(deck_path.read_text(encoding="utf-8"))
    editorial = validate_cardnews_editorial_deck(deck)
    for index, slide in enumerate(deck["slides"]):
        expected = builder.layout_for(index, slide)
        if slide.get("layoutType") != expected:
            raise RuntimeError(f"{index + 1}번 카드 레이아웃 정본 불일치: {slide.get('layoutType')} != {expected}")
        markup = builder.slide_html(index, slide, TOPIC)
        for field in ("title", "head", "desc", "sub", "quote", "cta1", "cta2"):
            value = slide.get("f", {}).get(field)
            if value and builder.esc(value) not in markup:
                raise RuntimeError(f"{index + 1}번 카드의 {field} 원고가 렌더 HTML에서 누락됐습니다.")

    (ROOT / "07_index.html").write_text(
        builder.make_html(SOURCE_ID, deck, TOPIC), encoding="utf-8"
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *a, **kw: Quiet(*a, directory=str(ROOT), **kw)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    PROVIDER_LOCK.touch(exist_ok=True)
    try:
        with PROVIDER_LOCK.open("a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            metrics = builder.render_source(
                SOURCE_ID,
                f"http://127.0.0.1:{server.server_port}/07_index.html",
                ROOT,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    builder.write_json(ROOT / "08_dom_metrics.json", metrics)

    paths = [ROOT / "png" / f"{index:02d}.png" for index in range(1, 11)]
    ocr = subprocess.run(
        ["swift", str(OCR_PATH), *map(str, paths)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if ocr.returncode != 0:
        raise RuntimeError("Vision OCR 실패: " + ocr.stderr[-2000:])
    rows = {row["path"]: row for row in json.loads(ocr.stdout)}
    cards = []
    for index, (slide, path, metric) in enumerate(zip(deck["slides"], paths, metrics), 1):
        observed = " ".join(item["text"] for item in rows[str(path)].get("observations", []))
        coverage = builder.token_coverage(builder.expected_text(slide), observed)
        confidence_rows = rows[str(path)].get("observations", [])
        confidence = sum(item["confidence"] for item in confidence_rows) / max(1, len(confidence_rows))
        with Image.open(path) as image:
            dimensions = list(image.size)
        checks = {
            **metric["metrics"]["checks"],
            "pngExactly1080Square": dimensions == [1080, 1080],
            "notFourByFive": dimensions != [1080, 1350],
            "max30KoreanEojeol": builder.card_words(slide) <= 30,
            "ocrTokenCoverageAtLeast45Pct": coverage >= 0.45,
            "ocrMeanConfidenceAtLeast50Pct": confidence >= 0.50,
        }
        cards.append(
            {
                "card": index,
                "file": str(path),
                "layoutType": slide["layoutType"],
                "dimensions": dimensions,
                "sha256": builder.sha256(path),
                "ocrTokenCoverage": round(coverage, 4),
                "ocrMeanConfidence": round(confidence, 4),
                "checks": checks,
                "status": "PASS" if all(checks.values()) else "FAIL",
            }
        )
    sheet = builder.contact_sheet(ROOT, SOURCE_ID, TOPIC)
    result = {
        "sourceId": SOURCE_ID,
        "topic": TOPIC,
        "status": "PASS" if all(card["status"] == "PASS" for card in cards) else "FAIL",
        "editorial": editorial,
        "contactSheet": str(sheet),
        "cards": cards,
    }
    builder.write_json(ROOT / "09_machine_validation.json", result)
    if result["status"] != "PASS":
        failed = {
            card["card"]: [name for name, passed in card["checks"].items() if not passed]
            for card in cards
            if card["status"] != "PASS"
        }
        raise RuntimeError(f"카드뉴스 머신 게이트 실패: {failed}")
    print(json.dumps({"status": "PASS", "sourceId": SOURCE_ID}, ensure_ascii=False))


def main(argv=None):
    import argparse
    from content_lineage import validate_cardnews_origin
    global ROOT, SOURCE_ID, TOPIC
    parser = argparse.ArgumentParser(description="Shared approved Aside square-card renderer")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    ROOT = args.root.resolve()
    deck = json.loads((ROOT / "04_cardnews_deck.json").read_text())
    validate_cardnews_origin(deck)
    SOURCE_ID = deck["content_lineage"]["source_key"]
    TOPIC = deck["slides"][0]["f"]["title"]
    if (ROOT / "png").exists():
        raise RuntimeError("existing renders are preserved; choose a fresh candidate directory")
    return render()

if __name__ == "__main__":
    main()
