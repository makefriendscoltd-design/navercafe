"""Fill or publish a YouTube Community post through Aside CLI.

The default is intentionally non-publishing: it fills the composer and leaves
the final Post button untouched.  ``--publish`` is required for the external
write, and ``--expected-channel`` should be supplied with it.
"""

from __future__ import annotations

import argparse
import configparser
import json
import hashlib
import fcntl
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from aside_browser import post_to_youtube_community
from youtube_community_verification import inspect_posts
from external_publish_tracking import track_publication


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.ini"


def _save_receipt(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def publish_verified(text: str, images: list[str], *, source_key: str, receipt: Path,
                     expected_channel: str, community_url: str = '', account=None,
                     verify_only: bool = False, record_observation: bool = False) -> dict:
    """A reserved attempt may only be reconciled, never blindly posted twice."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', source_key):
        raise ValueError('Community source identity is invalid')
    if len(images) != 10:
        raise ValueError('Community requires exactly ten approved cards')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    identity = dict(source_key=source_key, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    images_sha256=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images])
    with receipt.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = json.loads(receipt.read_text()) if receipt.exists() else {}
        if prior and any(prior.get(k) != v for k, v in identity.items()):
            raise RuntimeError('Existing Community receipt binds different content; review required')
        if prior.get('status') == 'published' and prior.get('verified') is True and not verify_only:
            if prior.get('newly_published') and not prior.get('crm_tracked') and not verify_only:
                prior['crm_tracked'] = track_publication('youtube_community', source_key)
                _save_receipt(receipt, prior)
            return prior
        newly_published = False
        scan = inspect_posts(text, len(images), source_key, expected_channel,
                             community_url=community_url, account=account)
        if scan.get('status') != 'verified':
            if verify_only:
                return {'status': 'unverified', 'source_key': source_key, 'scan': scan}
            # Older scheduled posts are not necessarily visible on the public
            # feed. Their evidence must never be mistaken for permission to post.
            legacy = any(re.search(r'youtube\.com/post/[A-Za-z0-9_-]+', p.read_text(encoding='utf-8'))
                         for p in receipt.parent.rglob('*.json') if p != receipt)
            if prior or legacy or scan.get('related_count') or not scan.get('exhausted'):
                raise RuntimeError('Community result ambiguous; verify only, do not repost')
            reservation = dict(identity, status='reserved_verify_only', verified=False,
                               attempted_at=datetime.now(timezone.utc).isoformat())
            _save_receipt(receipt, reservation)
            # The browser's old success toast is not accepted as a provider receipt.
            post_to_youtube_community(text, images, publish=True,
                                      community_url=community_url, expected_channel=expected_channel,
                                      account=account)
            newly_published = True
            scan = inspect_posts(text, len(images), source_key, expected_channel,
                                 community_url=community_url, account=account)
        provider = scan.get('provider') or {}
        if scan.get('status') != 'verified' or not provider.get('verified') or not re.fullmatch(
                r'https://www.youtube.com/post/[A-Za-z0-9_-]+', provider.get('url', '')):
            raise RuntimeError('Community direct post/body/cards verification failed; no repost')
        result = dict(identity, **provider, status='published', newly_published=newly_published)
        if verify_only:
            if record_observation:
                result['crm_status'] = 'historical_observation_no_new_send'
                _save_receipt(receipt, result)
            return result
        # Save provider success before tracking so tracker failure never reposts.
        _save_receipt(receipt, result)
        result['crm_tracked'] = track_publication('youtube_community', source_key) if newly_published else False
        if not newly_published:
            result['crm_status'] = 'historical_observation_no_new_send'
        _save_receipt(receipt, result)
        return result


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
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-key", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--reconcile", action="store_true", help="Read and save existing provider proof; never post")
    args = parser.parse_args(argv)

    text_path = Path(args.text_file).expanduser().resolve()
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        parser.error("--text-file 내용이 비어 있습니다.")
    if len(args.images) > 10:
        parser.error("YouTube 커뮤니티 이미지는 최대 10장입니다.")

    cfg = load_config()
    expected = args.expected_channel or cfg["expected_channel"]
    if args.publish and (args.verify_only or args.reconcile):
        parser.error('--publish cannot be combined with read-only verification')
    if (args.publish or args.verify_only or args.reconcile) and not expected:
        parser.error("--publish에는 오발행 방지를 위해 --expected-channel이 필요합니다.")

    if args.publish or args.verify_only or args.reconcile:
        key = args.source_key or next(iter(re.findall(r'(?:youtu\.be/|[?&]v=)([A-Za-z0-9_-]{11})', text)), '')
        result = publish_verified(text, args.images, source_key=key,
                                  receipt=args.receipt or text_path.parent / 'provider/publish_receipt.json',
                                  expected_channel=expected, community_url=args.community_url or cfg['community_url'],
                                  account=args.aside_account or cfg['aside_account'] or None,
                                  verify_only=args.verify_only or args.reconcile,
                                  record_observation=args.reconcile)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('status') == 'published' else 1

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
