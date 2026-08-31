"""Fill or publish a YouTube Community post through Aside CLI.

The default is intentionally non-publishing: it fills the composer and leaves
the final Post button untouched.  ``--publish`` is required for the external
write, and ``--expected-channel`` should be supplied with it.
"""

from __future__ import annotations

import argparse
import configparser
import json
import sys
from pathlib import Path

from aside_browser import post_to_youtube_community


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.ini"


def load_config() -> dict[str, str]:
    cfg = configparser.RawConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return {
        "community_url": cfg.get("YOUTUBE", "community_url", fallback="").strip(),
        "expected_channel": cfg.get("YOUTUBE", "expected_channel", fallback="").strip(),
        "aside_account": cfg.get("BROWSER", "aside_account", fallback="").strip(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--images", nargs="*", default=[])
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--community-url", default="")
    parser.add_argument("--expected-channel", default="")
    parser.add_argument("--aside-account", default="")
    parser.add_argument("--preview-path", default="")
    args = parser.parse_args(argv)

    text_path = Path(args.text_file).expanduser().resolve()
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        parser.error("--text-file 내용이 비어 있습니다.")
    if len(args.images) > 10:
        parser.error("YouTube 커뮤니티 이미지는 최대 10장입니다.")

    cfg = load_config()
    expected = args.expected_channel or cfg["expected_channel"]
    if args.publish and not expected:
        parser.error("--publish에는 오발행 방지를 위해 --expected-channel이 필요합니다.")

    result = post_to_youtube_community(
        text,
        args.images,
        publish=args.publish,
        community_url=args.community_url or cfg["community_url"],
        expected_channel=expected,
        preview_path=args.preview_path or None,
        account=args.aside_account or cfg["aside_account"] or None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
