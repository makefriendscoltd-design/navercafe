# -*- coding: utf-8 -*-
"""
Fill a YouTube Community post composer with text and up to 10 images.

This uses a real Chrome window because YouTube Community posting has no stable
public posting API. By default it fills the composer and leaves the final
"Post" button untouched.
"""

import argparse
import configparser
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.ini"
DEFAULT_PROFILE_DIR = SCRIPT_DIR / "chrome_profile_youtube"


def _abs_existing_files(paths, limit=10):
    out = []
    for p in paths[:limit]:
        path = Path(p).resolve()
        if not path.exists():
            raise FileNotFoundError(str(path))
        out.append(str(path))
    return out


def load_youtube_config():
    cfg = configparser.RawConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return {
        "community_url": cfg.get("YOUTUBE", "community_url", fallback="").strip(),
        "studio_url": cfg.get("YOUTUBE", "studio_url", fallback="https://studio.youtube.com/").strip(),
        "chrome_cdp_url": cfg.get("YOUTUBE", "chrome_cdp_url", fallback="").strip(),
        "profile_dir": cfg.get("YOUTUBE", "profile_dir", fallback=str(DEFAULT_PROFILE_DIR)).strip(),
    }


def _first_visible(page, selectors, timeout=9000):
    last_error = None
    for sel in selectors:
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="visible", timeout=timeout)
            return loc.first
        except Exception as e:
            last_error = e
    if last_error:
        raise last_error
    raise RuntimeError("visible locator not found")


def _goto_composer(page, cfg):
    if cfg.get("community_url"):
        page.goto(cfg["community_url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        return

    page.goto(cfg.get("studio_url") or "https://studio.youtube.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)

    href = page.evaluate(
        """() => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            const hit = links.find(a => /show_create_dialog=1/.test(a.href) ||
                /\\/posts\\?show_create_dialog=1/.test(a.href) ||
                (a.innerText || '').includes('게시물 작성'));
            return hit ? hit.href : '';
        }"""
    )
    if href:
        page.goto(href, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        return

    community = page.get_by_text("커뮤니티", exact=True)
    if community.count():
        community.first.click()
        page.wait_for_timeout(4000)
        return

    raise RuntimeError("YouTube 게시물 작성 진입점을 찾지 못했습니다.")


def _activate_textbox(page):
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        """() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            return buttons.some(b => {
                const text = (b.innerText || b.getAttribute('aria-label') || '').trim();
                return text.includes('어떤 생각을') || text.includes('공유해 보세요') || text.includes('무엇을');
            }) || Array.from(document.querySelectorAll('[contenteditable="true"], textarea')).some(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
        }""",
        timeout=20000,
    )
    clicked = page.evaluate(
        """() => {
            const edit = Array.from(document.querySelectorAll('[contenteditable="true"], textarea')).find(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (edit) {
                edit.click();
                edit.focus();
                return true;
            }
            const buttons = Array.from(document.querySelectorAll('button'));
            const button = buttons.find(b => {
                const text = (b.innerText || b.getAttribute('aria-label') || '').trim();
                return text.includes('어떤 생각을') || text.includes('공유해 보세요') || text.includes('무엇을');
            });
            if (!button) return false;
            button.click();
            return true;
        }"""
    )
    if not clicked:
        raise RuntimeError("YouTube 게시글 입력창을 찾지 못했습니다.")
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('[contenteditable="true"], textarea')).some(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }) || document.activeElement""",
        timeout=10000,
    )
    page.wait_for_timeout(600)


def _set_post_text(page, text):
    _activate_textbox(page)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(text)
    page.wait_for_timeout(800)

    ok = page.evaluate(
        """() => {
            const active = document.activeElement;
            const editables = Array.from(document.querySelectorAll('[contenteditable="true"], textarea'));
            return editables.some(el => ((el.innerText || el.value || '').trim().length > 0)) ||
                ((active && (active.innerText || active.value || '').trim().length > 0));
        }"""
    )
    if not ok:
        raise RuntimeError("YouTube 게시글 텍스트 입력을 확인하지 못했습니다.")


def _upload_images(page, image_paths):
    if not image_paths:
        return

    files = _abs_existing_files(image_paths, limit=10)

    try:
        inputs = page.locator('input[type="file"][multiple], input[type="file"]')
        inputs.first.wait_for(state="attached", timeout=10000)
        inputs.first.set_input_files(files)
    except Exception:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            chooser_btn = page.get_by_role("button", name="컴퓨터에서 선택", exact=True)
            if chooser_btn.count():
                chooser_btn.first.click()
            else:
                image_btn = page.get_by_role("button", name="이미지 추가")
                if image_btn.count():
                    image_btn.first.click()
                    page.wait_for_timeout(500)
                    page.get_by_role("button", name="컴퓨터에서 선택", exact=True).first.click()
                else:
                    _first_visible(page, ['button:has-text("이미지")', 'button[aria-label*="이미지"]']).click()
        fc_info.value.set_files(files)

    page.wait_for_timeout(2500)


def _wait_post_enabled(page, timeout_ms=120000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        enabled = page.evaluate(
            """() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const post = buttons.find(b => (b.innerText || b.getAttribute('aria-label') || '').trim() === '게시');
                return !!post && !post.disabled && post.getAttribute('aria-disabled') !== 'true';
            }"""
        )
        if enabled:
            return True
        page.wait_for_timeout(1000)
    return False


def _click_publish(page):
    if not _wait_post_enabled(page):
        raise RuntimeError("게시 버튼이 활성화되지 않았습니다. 이미지 업로드가 끝났는지 확인하세요.")
    post_btn = page.get_by_role("button", name="게시")
    if post_btn.count():
        post_btn.first.click()
    else:
        page.locator('button:has-text("게시")').first.click()
    page.wait_for_timeout(5000)


def _assert_active_channel(page, expected_channel):
    expected = (expected_channel or "").strip()
    if not expected:
        return

    page.wait_for_timeout(1500)
    visible_text = page.evaluate(
        """() => [
            document.title || '',
            document.body ? (document.body.innerText || '') : '',
            ...Array.from(document.querySelectorAll('[aria-label], [title]')).map(el =>
                `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`
            )
        ].join('\\n')"""
    )
    if expected not in visible_text:
        raise RuntimeError(f"active YouTube channel is not confirmed as {expected}")


def post_to_youtube_community(post_text, image_paths, publish=False,
                              community_url="", cdp_url="", profile_dir="",
                              expected_channel=""):
    cfg = load_youtube_config()
    if community_url:
        cfg["community_url"] = community_url
    if cdp_url:
        cfg["chrome_cdp_url"] = cdp_url
    if profile_dir:
        cfg["profile_dir"] = profile_dir

    with sync_playwright() as p:
        browser = None
        if cfg.get("chrome_cdp_url"):
            browser = p.chromium.connect_over_cdp(cfg["chrome_cdp_url"])
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            context = p.chromium.launch_persistent_context(
                str(Path(cfg["profile_dir"]).resolve()),
                channel="chrome",
                headless=False,
                viewport={"width": 1440, "height": 1000},
                args=["--disable-blink-features=AutomationControlled"],
            )

        page = context.pages[0] if context.pages else context.new_page()
        try:
            _goto_composer(page, cfg)
            if publish:
                _assert_active_channel(page, expected_channel)
            _set_post_text(page, post_text)
            _upload_images(page, image_paths[:10])

            if publish:
                _assert_active_channel(page, expected_channel)
                _click_publish(page)
                return {"status": "published", "url": page.url}
            return {"status": "filled", "url": page.url, "images": min(len(image_paths), 10)}
        finally:
            if publish:
                context.close()
                if browser:
                    browser.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--images", nargs="*", default=[])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--community-url", default="")
    ap.add_argument("--cdp-url", default="")
    ap.add_argument("--profile-dir", default="")
    ap.add_argument("--expected-channel", default="")
    args = ap.parse_args(argv)

    text = Path(args.text_file).read_text(encoding="utf-8")
    result = post_to_youtube_community(
        text,
        args.images,
        publish=args.publish,
        community_url=args.community_url,
        cdp_url=args.cdp_url,
        profile_dir=args.profile_dir,
        expected_channel=args.expected_channel,
    )
    print(result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
