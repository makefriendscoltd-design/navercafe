"""Aside CLI based browser automation for Naver Cafe and YouTube.

The module deliberately never sends account passwords to Aside.  It reuses the
browser profile selected in the Aside app and returns ``login_required`` when
that profile is signed out.  All workflows emit one machine-readable marker so
Python callers do not have to parse Aside's human-oriented status messages.
"""

from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


RESULT_MARKER = "__ASIDE_RESULT__"
DEFAULT_TIMEOUT = 180


class AsideError(RuntimeError):
    """Raised when the Aside CLI or the requested browser workflow fails."""


class AsideLoginRequired(AsideError):
    """Raised when the selected Aside browser profile is not signed in."""


def resolve_aside_cli() -> str | None:
    """Return the configured/installed Aside executable without running it."""
    configured = os.environ.get("ASIDE_CLI_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("aside") or "",
        str(Path.home() / ".local" / "bin" / "aside"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    return None


def aside_available() -> bool:
    return resolve_aside_cli() is not None


def _payload_expression(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return (
        "JSON.parse(new TextDecoder().decode(Uint8Array.from("
        f"atob('{encoded}'), c => c.charCodeAt(0))))"
    )


def _parse_result(output: str) -> dict[str, Any]:
    matches = re.findall(rf"{re.escape(RESULT_MARKER)}([^\r\n]+)", output)
    if not matches:
        tail = "\n".join(output.strip().splitlines()[-8:])
        raise AsideError(f"Aside 결과 마커를 찾지 못했습니다.\n{tail}")
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise AsideError("Aside 결과 JSON을 해석하지 못했습니다.") from exc
    if not isinstance(data, dict):
        raise AsideError("Aside 결과가 객체 형식이 아닙니다.")
    if data.get("status") == "login_required":
        site = data.get("site") or "사이트"
        raise AsideLoginRequired(
            f"Aside 브라우저에서 {site} 로그인이 필요합니다. 열린 로그인 탭에서 "
            "로그인을 마친 뒤 다시 실행하세요."
        )
    if data.get("status") == "error":
        raise AsideError(str(data.get("message") or "Aside 브라우저 작업이 실패했습니다."))
    return data


def run_repl(
    code: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    account: str | None = None,
) -> dict[str, Any]:
    """Run deterministic JavaScript in Aside and return the marked JSON result."""
    executable = resolve_aside_cli()
    if not executable:
        raise AsideError(
            "Aside CLI를 찾을 수 없습니다. Aside > Settings > Developers에서 CLI를 설치하세요."
        )
    command = [executable, "repl"]
    selected_account = (account or os.environ.get("ASIDE_ACCOUNT", "")).strip()
    if selected_account:
        command.extend(["--account", selected_account])
    # Feed one async expression through stdin.  Passing generated code as a
    # command-line argument hits macOS ARG_MAX once image bytes are embedded.
    expression = f"(async()=>{{\n{code}\n}})()"
    repl_input = "await eval(" + json.dumps(expression, ensure_ascii=False) + ");\n"
    try:
        proc = subprocess.run(
            command,
            input=repl_input,
            cwd=str(cwd or Path.cwd()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AsideError(f"Aside 작업이 {timeout}초 안에 끝나지 않았습니다.") from exc
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    if proc.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-10:])
        raise AsideError(f"Aside CLI가 종료 코드 {proc.returncode}로 실패했습니다.\n{tail}")
    return _parse_result(output)


@contextlib.contextmanager
def staged_uploads(paths: Iterable[str | os.PathLike[str]]):
    """Copy upload files into an isolated Aside session directory.

    Aside intentionally prevents uploads outside the current session directory.
    A temporary directory keeps that boundary narrow and is deleted afterwards.
    """
    resolved = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        resolved.append(path)
    with tempfile.TemporaryDirectory(prefix="navercafe-aside-") as temp:
        root = Path(temp)
        names = []
        for index, source in enumerate(resolved, 1):
            suffix = source.suffix.lower() or ".bin"
            name = f"upload-{index:02d}{suffix}"
            shutil.copy2(source, root / name)
            names.append(name)
        yield root, names


def _embedded_uploads(root: Path, names: Iterable[str]) -> list[dict[str, str]]:
    """Build Aside FilePayload data; path uploads currently arrive as 0 bytes."""
    payloads = []
    for name in names:
        path = root / name
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        payloads.append({
            "name": name,
            "mimeType": mime_type,
            "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    return payloads


def _save_preview(result: dict[str, Any], preview_path: str | os.PathLike[str] | None) -> dict[str, Any]:
    encoded = str(result.pop("_preview_png", "") or "")
    cards_encoded = str(result.pop("_preview_cards_png", "") or "")
    if not preview_path:
        return result
    if not encoded:
        raise AsideError("게시 직전 화면 캡처를 받지 못했습니다.")
    path = Path(preview_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded))
    result["preview_path"] = str(path)
    if cards_encoded:
        cards_path = path.with_name(f"{path.stem}-cards{path.suffix}")
        cards_path.write_bytes(base64.b64decode(cards_encoded))
        result["preview_cards_path"] = str(cards_path)
    return result


JS_COMMON = r"""
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const emit = value => console.log('__ASIDE_RESULT__' + JSON.stringify(value));
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
};
async function contextsFor(p) {
  const out = [p];
  try { for (const frame of p.frames()) if (frame !== p.mainFrame?.()) out.push(frame); } catch (_) {}
  return out;
}
async function findContext(p, selector) {
  for (const ctx of await contextsFor(p)) {
    try {
      const loc = ctx.locator(selector);
      const count = await loc.count();
      for (let i = 0; i < count; i++) {
        const item = loc.nth(i);
        if (await item.isVisible()) return {ctx, loc: item};
      }
    } catch (_) {}
  }
  return null;
}
async function findAnyContext(p, selector) {
  for (const ctx of await contextsFor(p)) {
    try {
      const loc = ctx.locator(selector);
      if (await loc.count()) return {ctx, loc: loc.nth(0)};
    } catch (_) {}
  }
  return null;
}
async function waitForContext(p, selector, timeoutMs=20000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const found = await findContext(p, selector);
    if (found) return found;
    await sleep(500);
  }
  return null;
}
async function pageLooksLoggedOut(p, site) {
  const url = p.url();
  if (/nidlogin|accounts\.google\.com|ServiceLogin/i.test(url)) return true;
  try {
    return await p.evaluate(siteName => {
      const text = document.body?.innerText || '';
      if (siteName === 'naver') {
        const loginLink = document.querySelector('a[href*="nidlogin"],a[class*="link_login"]');
        const logoutControl = document.querySelector(
          '[href*="logout"],button[class*="btn_logout"],[class*="logout"]'
        );
        // Naver keeps a hidden login link in the homepage DOM even after login.
        // A visible account logout control/text is therefore the stronger signal.
        if (logoutControl || /로그아웃/.test(text)) return false;
        if (loginLink) return true;
        return /네이버 로그인|NAVER\s*로그인/.test(text) && !/로그아웃/.test(text);
      }
      return /Sign in|로그인/.test(text) && /YouTube|Google/.test(text);
    }, site);
  } catch (_) { return false; }
}
"""


def check_login(site: str, *, keep_login_tab: bool = False) -> dict[str, Any]:
    """Check a Naver or YouTube session without reading cookies or credentials."""
    if site not in {"naver", "youtube"}:
        raise ValueError("site must be 'naver' or 'youtube'")
    url = "https://www.naver.com/" if site == "naver" else "https://studio.youtube.com/"
    login_url = (
        "https://nid.naver.com/nidlogin.login"
        if site == "naver"
        else "https://accounts.google.com/ServiceLogin?service=youtube"
    )
    payload = {"site": site, "url": url, "loginUrl": login_url, "keep": keep_login_tab}
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const p = await openTab(payload.url);
await sleep(1800);
const loggedOut = await pageLooksLoggedOut(p, payload.site);
if (loggedOut && payload.keep) {
  await p.goto(payload.loginUrl);
  emit({status:'login_required', site:payload.site});
} else {
  const result = {status:'ok', site:payload.site, logged_in:!loggedOut, url:p.url()};
  await p.close();
  emit(result);
}
"""
    return run_repl(code, timeout=45)


def capture_youtube_frames(
    youtube_url: str,
    image_count: int,
    out_dir: str | os.PathLike[str],
    *,
    account: str | None = None,
) -> list[str]:
    """Capture public YouTube player frames through Aside without reading cookies."""
    if image_count <= 0:
        return []
    video_id = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", youtube_url or "")
    if not video_id:
        raise ValueError("올바른 YouTube URL이 아닙니다.")
    root = Path(out_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    count = min(int(image_count), 10)
    payload = {
        "url": f"https://www.youtube.com/watch?v={video_id.group(1)}",
        "count": count,
    }
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const p = await openTab(payload.url);
await sleep(2500);
const found = await waitForContext(p, 'video', 30000);
if (!found) {
  emit({status:'error', message:'YouTube 영상 플레이어를 찾지 못했습니다.'});
} else {
  const duration = await found.ctx.evaluate(() => {
    const v=document.querySelector('video'); return Number(v?.duration || 0);
  });
  if (!duration || !Number.isFinite(duration)) {
    emit({status:'error', message:'YouTube 영상 길이를 확인하지 못했습니다.'});
  } else {
    const shots=[];
    for (let i=0; i<payload.count; i++) {
      const seconds = duration * (i + 1) / (payload.count + 1);
      await found.ctx.evaluate(async value => {
        const v=document.querySelector('video');
        if (!v) return;
        v.muted=true;
        v.currentTime=Math.max(0, Math.min(value, v.duration - 1));
        try { await v.play(); } catch (_) {}
      }, seconds);
      await sleep(1800);
      const box=await found.loc.boundingBox();
      const png=await p.screenshot();
      shots.push({data:Buffer.from(png).toString('base64'), box});
    }
    await p.close();
    emit({status:'captured', shots, count:shots.length});
  }
}
"""
    result = run_repl(code, cwd=root, timeout=max(90, count * 15), account=account)
    if result.get("status") != "captured":
        raise AsideError(f"YouTube 장면 캡처를 확인하지 못했습니다: {result}")
    shots = result.get("shots", [])
    if len(shots) != count:
        raise AsideError("Aside가 요청한 장수만큼 YouTube 화면을 캡처하지 못했습니다.")
    from io import BytesIO
    from PIL import Image
    paths = []
    for index, shot in enumerate(shots, 1):
        raw = base64.b64decode(shot.get("data", ""))
        image = Image.open(BytesIO(raw)).convert("RGB")
        box = shot.get("box") or {}
        x = max(0, int(round(float(box.get("x", 0)))))
        y = max(0, int(round(float(box.get("y", 0)))))
        width = max(1, int(round(float(box.get("width", image.width)))))
        height = max(1, int(round(float(box.get("height", image.height)))))
        right = min(image.width, x + width)
        bottom = min(image.height, y + height)
        if right > x and bottom > y:
            image = image.crop((x, y, right, bottom))
        path = root / f"youtube-frame-{index:02d}.jpg"
        image.save(path, "JPEG", quality=92)
        paths.append(str(path.resolve()))
    return paths


def _plain_body_chunks(body: str) -> list[str]:
    """Keep visual section boundaries while removing generator-only markers."""
    chunks = []
    for chunk in (body or "").split("[IMAGE_HERE]"):
        clean = re.sub(r"\[/?(?:BOLD|HIGHLIGHT)\]", "", chunk)
        clean = re.sub(r"\[BLOCKQUOTE\](.*?)\[/BLOCKQUOTE\]", r"\n\1\n", clean, flags=re.S)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        chunks.append(clean)
    return chunks


def _compose_naver_body(
    body: str,
    *,
    cta_text: str = "",
    cta_link_text: str = "",
    cta_link_url: str = "",
    source_label: str = "▶ 원본 영상",
    source_url: str = "",
) -> str:
    """Append the owner's CTA and the original source link without dropping markers."""
    parts = [(body or "").rstrip()]
    cta_parts = [part.strip() for part in (cta_text, cta_link_text, cta_link_url) if part.strip()]
    if cta_parts:
        parts.append("\n\n".join(cta_parts))
    if source_url.strip():
        label = source_label.strip() or "▶ 원본 영상"
        parts.append(f"{label}\n{source_url.strip()}")
    return "\n\n\n".join(part for part in parts if part).strip()


def _naver_structure_expectation(body: str) -> tuple[list[str], list[str]]:
    """Return the exact top-level component order expected in SmartEditor."""
    sequence: list[str] = []
    quote_texts: list[str] = []
    tokens = re.split(
        r"(\[BLOCKQUOTE\][\s\S]*?\[/BLOCKQUOTE\]|\[IMAGE_HERE\])",
        body or "",
    )
    for token in tokens:
        if not token:
            continue
        quote = re.fullmatch(r"\[BLOCKQUOTE\]([\s\S]*?)\[/BLOCKQUOTE\]", token)
        if quote:
            sequence.append("quote")
            quote_texts.append(quote.group(1).splitlines()[0].strip()[:30])
        elif token == "[IMAGE_HERE]":
            sequence.append("image")
        elif re.sub(r"\[/?(?:BOLD|HIGHLIGHT)\]", "", token).strip():
            sequence.append("text")
    return sequence, quote_texts


def post_to_naver_cafe(
    title: str,
    body: str,
    image_paths: Iterable[str | os.PathLike[str]],
    *,
    cafe_url: str,
    cta_text: str = "",
    cta_link_text: str = "",
    cta_link_url: str = "",
    source_label: str = "▶ 원본 영상",
    source_url: str = "",
    board_name: str = "",
    bold_enabled: bool = True,
    highlight_enabled: bool = True,
    highlight_color: str = "#ffff00",
    publish: bool = True,
    save_draft: bool = False,
    preview_path: str | os.PathLike[str] | None = None,
    account: str | None = None,
    visible_tab_id: str = "",
    prefer_path_uploads: bool = False,
    precheck_drafts: bool = True,
) -> dict[str, Any]:
    """Fill, persist as a Naver draft, or publish through the signed-in profile."""
    if publish and save_draft:
        raise ValueError("네이버 카페 글은 임시등록과 발행을 동시에 요청할 수 없습니다.")
    final_body = _compose_naver_body(
        body,
        cta_text=cta_text,
        cta_link_text=cta_link_text,
        cta_link_url=cta_link_url,
        source_label=source_label,
        source_url=source_url,
    )
    # SmartEditor only expands links into OG/oEmbed cards when they arrive as
    # a real clipboard paste.  Keep URLs out of the sequentially typed body and
    # append them through the editor after the article structure is complete.
    editor_body = _compose_naver_body(
        body,
        cta_text=cta_text,
        cta_link_text=cta_link_text,
        cta_link_url="",
        source_label=source_label,
        source_url="",
    )
    highlight_keywords = [
        re.sub(r"\[/?BOLD\]", "", item).strip()
        for item in re.findall(r"\[HIGHLIGHT\](.*?)\[/HIGHLIGHT\]", final_body, re.S)
    ]
    highlight_keywords = [item for item in highlight_keywords if item]
    bold_keywords = [
        re.sub(r"\[/?HIGHLIGHT\]", "", item).strip()
        for item in re.findall(r"\[BOLD\](.*?)\[/BOLD\]", final_body, re.S)
    ]
    bold_keywords = [item for item in bold_keywords if item]
    expected_sequence, expected_quote_texts = _naver_structure_expectation(editor_body)
    if cta_link_url.strip() and (not expected_sequence or expected_sequence[-1] != "text"):
        expected_sequence.append("text")
    if source_url.strip() and (
        cta_link_url.strip() or not expected_sequence or expected_sequence[-1] != "text"
    ):
        expected_sequence.append("text")

    decoded = cafe_url or ""
    club = re.search(r"clubid[=&](\d+)|cafes/(\d+)", decoded, re.I)
    menu = re.search(r"menuid[=&](\d+)|menus/(\d+)|boardId[=&](\d+)", decoded, re.I)
    clubid = next((g for g in club.groups() if g), "") if club else ""
    menuid = next((g for g in menu.groups() if g), "") if menu else ""
    write_url = (
        f"https://cafe.naver.com/ca-fe/cafes/{clubid}/menus/{menuid}/articles/write?boardType=L"
        if clubid and menuid
        else ""
    )

    with staged_uploads(image_paths) as (upload_dir, upload_names):
        image_payloads = (
            [
                {
                    "name": name,
                    "mimeType": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "path": name,
                }
                for name in upload_names
            ]
            if prefer_path_uploads
            else _embedded_uploads(upload_dir, upload_names)
        )
        payload = {
            "title": title,
            "body": editor_body,
            "images": image_payloads,
            "cafeUrl": cafe_url,
            "writeUrl": write_url,
            "boardName": board_name.strip(),
            "ctaLinkUrl": cta_link_url.strip(),
            "sourceLabel": source_label.strip() or "▶ 원본 영상",
            "sourceUrl": source_url.strip(),
            "boldEnabled": bool(bold_enabled),
            "highlightEnabled": bool(highlight_enabled),
            "highlightColor": highlight_color or "#ffff00",
            "highlightKeywords": highlight_keywords,
            "boldKeywords": bold_keywords,
            "expectedQuotes": len(expected_quote_texts),
            "expectedBold": len(re.findall(r"\[BOLD\].*?\[/BOLD\]", final_body, re.S)),
            "expectedSequence": expected_sequence,
            "expectedQuoteTexts": expected_quote_texts,
            "publish": bool(publish),
            "saveDraft": bool(save_draft),
            "precheckDrafts": bool(precheck_drafts),
            "capturePreview": bool(preview_path and not publish),
            "visibleTabId": visible_tab_id.strip(),
        }
        code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const norm = value => (value || '').replace(/\s+/g, '').trim();
let workflowError = '';
let quoteFailureStage = '';
let writeUrl = payload.writeUrl;
let landing = null;

if (payload.boardName && !payload.visibleTabId) {
  landing = await openTab(payload.cafeUrl);
  await sleep(2500);
  if (await pageLooksLoggedOut(landing, 'naver')) {
    await landing.goto('https://nid.naver.com/nidlogin.login');
    emit({status:'login_required', site:'naver'});
    workflowError = '__emitted__';
  } else {
    let boardHref = '';
    for (const ctx of await contextsFor(landing)) {
      if (boardHref) break;
      try {
        boardHref = await ctx.evaluate(name => {
          const wanted=(name||'').replace(/\s+/g,'').trim();
          const links=[...document.querySelectorAll('a[href*="/menus/"],a[href*="menuid="]')];
          const hit=links.find(a=>(a.textContent||'').replace(/\s+/g,'').trim()===wanted);
          return hit?.href || '';
        }, payload.boardName);
      } catch (_) {}
    }
    const match = boardHref.match(/\/cafes\/(\d+)\/menus\/(\d+)/) ||
      boardHref.match(/clubid=(\d+).*menuid=(\d+)/i);
    if (!match) workflowError = `카페 게시판 '${payload.boardName}'을 찾지 못했습니다.`;
    else writeUrl = `https://cafe.naver.com/ca-fe/cafes/${match[1]}/menus/${match[2]}/articles/write?boardType=L`;
  }
}

let p = null;
if (!workflowError) {
  if (payload.visibleTabId) {
    try { p = await attachBrowserTab(payload.visibleTabId); }
    catch (_) { workflowError = '현재 보이는 네이버 카페 탭에 연결하지 못했습니다.'; }
  } else {
    p = await openTab(writeUrl || payload.cafeUrl);
  }
  if (landing) { try { await landing.close(); } catch (_) {} }
}
if (workflowError === '__emitted__') {
  // Login-required result was already emitted above; leave the login tab visible.
} else if (workflowError) {
  emit({status:'error', message:workflowError});
} else {
await sleep(2500);
if (await pageLooksLoggedOut(p, 'naver')) {
  await p.goto('https://nid.naver.com/nidlogin.login');
  emit({status:'login_required', site:'naver'});
} else {
  let editor = await waitForContext(p, '.textarea_input', 12000);
  if (!editor && !payload.writeUrl) {
    const href = await p.evaluate(() => {
      const els = [...document.querySelectorAll('a,button,[role=button],[role=link]')];
      const hit = els.find(el => ['글쓰기','카페글쓰기'].includes((el.textContent||'').trim())) ||
        document.querySelector('#writeFormBtn,a[href*="articles/write"],a[href*="ArticleWrite"]');
      if (!hit) return '';
      if (hit.href) return hit.href;
      hit.click();
      return '__clicked__';
    });
    if (href && href !== '__clicked__') p = await openTab(href);
    await sleep(2500);
    if (href === '__clicked__' && tabs.length) p = tabs[tabs.length - 1];
    editor = await waitForContext(p, '.textarea_input', 12000);
  }
  if (!editor) {
    emit({status:'error', message:'네이버 카페 글쓰기 에디터(.textarea_input)를 찾지 못했습니다.'});
  } else {
    if(payload.saveDraft&&payload.precheckDrafts){
      try{
        const countButton=editor.ctx.locator('.btn_temp_count');
        if(await countButton.count()){
          await countButton.first().click();await sleep(350);
          const duplicates=await editor.ctx.evaluate(title=>{
            const wanted=(title||'').replace(/\s+/g,'').trim();
            return [...document.querySelectorAll('.temp_item_title')]
              .filter(el=>(el.textContent||'').replace(/\s+/g,'').trim()===wanted).length;
          },payload.title);
          await countButton.first().click();
          try{await p.keyboard.press('Escape');}catch(_){}
          // Let the temporary-draft popover finish closing before the editor
          // receives its first real keyboard/clipboard action.
          await sleep(650);
          if(duplicates)workflowError=`동일 제목의 네이버 임시글이 ${duplicates}개 있어 새 임시글을 만들지 않았습니다.`;
        }
      }catch(_){workflowError='네이버 임시글 중복 여부를 확인하지 못했습니다.';}
    }
    let existingTitle='';
    try{existingTitle=(await editor.loc.evaluate(el=>el.value||'')).trim();}catch(_){}
    if(existingTitle&&norm(existingTitle)!==norm(payload.title)){
      workflowError='기존에 작성 중인 다른 글이 있어 덮어쓰지 않았습니다.';
    }else{
      await editor.loc.fill(payload.title);
    }
    let selectedBoard = '';
    try {
      selectedBoard = await editor.ctx.evaluate(() => {
        const boxes=document.querySelectorAll('.FormSelectButton');
        const button=boxes[0]?.querySelector('button');
        return (button?.textContent||'').trim();
      });
    } catch (_) {}
    if (payload.boardName && norm(selectedBoard) !== norm(payload.boardName)) {
      workflowError = `게시판 확인 실패: 기대 '${payload.boardName}', 현재 '${selectedBoard || '확인 불가'}'`;
    }
    const bodySelector = '.se-placeholder,.se-text-paragraph';
    let bodyFound = workflowError ? null : await waitForContext(p, bodySelector, 12000);
    if (!bodyFound) {
      emit({status:'error', message:workflowError || '네이버 SmartEditor 본문 입력 영역을 찾지 못했습니다.'});
    } else {
      // waitForContext may return the visible placeholder span. Normalize it
      // to SmartEditor's real paragraph before any keyboard or quote action.
      const normalizeBodyParagraph = async found => {
        if(!found)return found;
        try{
          const isParagraph=await found.loc.evaluate(el=>el.classList.contains('se-text-paragraph'));
          if(isParagraph)return found;
          const paragraphs=found.ctx.locator(
            '.se-components-wrap .se-component.se-text .se-text-paragraph'
          );
          const count=await paragraphs.count();
          if(count)return {ctx:found.ctx,loc:paragraphs.nth(count-1)};
        }catch(_){}
        return found;
      };
      bodyFound=await normalizeBodyParagraph(bodyFound);
      let started = false;
      let uploadDebug = [];
      // A prior failed preview can be restored by SmartEditor.  Clear only
      // when its title is empty or exactly this article; unrelated work was
      // blocked above before any body mutation.
      try{
        const beforeClear=await bodyFound.ctx.evaluate(()=>{
          const components=[...document.querySelectorAll('.se-components-wrap .se-component')];
          const text=components.map(el=>(el.innerText||el.textContent||''))
            .join('\n').replace(/내용을 입력하세요\.?/g,'').replace(/출처 입력/g,'')
            .replace(/\u200b/g,'').trim();
          return {text,images:document.querySelectorAll('.se-component.se-image,.se-section-image').length,
            quotes:document.querySelectorAll('.se-component.se-quotation,.se-section-quotation').length,
            uploading:/전송중\.\.\./.test(document.querySelector('.se-content')?.innerText||'')};
        });
        // Do not Meta+A an already-empty provider paragraph: SmartEditor
        // leaves its visible placeholder focused but drops subsequent input.
        if(beforeClear.text||beforeClear.images||beforeClear.quotes||beforeClear.uploading){
          await bodyFound.loc.click();
          await p.keyboard.press('Meta+A');
          await p.keyboard.press('Backspace');
          await sleep(450);
        }
        bodyFound=await normalizeBodyParagraph(
          (await waitForContext(p, bodySelector, 4000))||bodyFound
        );
        const restored=await bodyFound.ctx.evaluate(()=>({
          images:document.querySelectorAll('.se-component.se-image,.se-section-image').length,
          quotes:document.querySelectorAll('.se-component.se-quotation,.se-section-quotation').length,
          uploading:/전송중\.\.\./.test(document.querySelector('.se-content')?.innerText||'')
        }));
        if(restored.images||restored.quotes||restored.uploading){
          workflowError='같은 제목으로 복원된 이전 작성 내용을 안전하게 초기화하지 못했습니다.';
        }
      }catch(_){
        workflowError='같은 제목으로 복원된 이전 작성 내용을 초기화하지 못했습니다.';
      }
      let activeParagraph=bodyFound.loc;
      let reuseCurrentParagraph=false;
      const typingTargetFor = async paragraph => {
        try{
          const placeholders=paragraph.locator('.se-placeholder');
          const placeholderCount=await placeholders.count();
          if(placeholderCount)return placeholders.nth(placeholderCount-1);
          const nodes=paragraph.locator('.__se-node');
          const nodeCount=await nodes.count();
          if(nodeCount){
            const node=nodes.nth(nodeCount-1);
            const box=await node.boundingBox();
            if(box&&box.width>0&&box.height>0)return node;
          }
        }catch(_){}
        return paragraph;
      };
      const focusTextParagraph = async paragraph => {
        activeParagraph=paragraph;
        const inputNode=await typingTargetFor(paragraph);
        const targetIsParagraph=await inputNode.evaluate(el=>
          el.classList.contains('se-text-paragraph'));
        // After quote escape or image insertion SmartEditor already owns a
        // native caret in the bare P. Clicking that P again drops the caret.
        if(targetIsParagraph)return true;
        try{
          await inputNode.click({force:true});
        }catch(_){
          return false;
        }
        await sleep(60);
        return true;
      };
      const isOutsideQuote = async paragraph => {
        try {
          return await paragraph.evaluate(el=>
            !el.closest('.se-component.se-quotation,.se-section-quotation'));
        } catch (_) { return false; }
      };
      const focusBodyParagraph = async paragraph => {
        if(!(await isOutsideQuote(paragraph)))return false;
        return await focusTextParagraph(paragraph);
      };
      const focusEnd = async () => {
        if(reuseCurrentParagraph&&activeParagraph){
          reuseCurrentParagraph=false;
          if(!(await focusBodyParagraph(activeParagraph)))return false;
          return true;
        }
        if (!started) {
          if(!(await focusBodyParagraph(bodyFound.loc)))return false;
          started = true;
          return true;
        }
        // Work only with top-level editor components. A quotation contains
        // editable descendant paragraphs which must never be reused as body.
        for (const ctx of [bodyFound.ctx]) {
          try {
            const components=ctx.locator('.se-components-wrap .se-component');
            const componentCount=await components.count();
            if(componentCount){
              const last=components.nth(componentCount-1);
              const state=await last.evaluate(el=>({
                isText:el.classList.contains('se-text'),
                text:(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim()
              }));
              if(state.isText&&!state.text){
                const paragraph=last.locator('.se-text-paragraph');
                const paragraphCount=await paragraph.count();
                if(paragraphCount){
                  if(!(await focusBodyParagraph(paragraph.nth(paragraphCount-1))))continue;
                  return true;
                }
              }
            }
            const edges=ctx.locator('.se-canvas-bottom-button');
            let edge=null;
            for(let edgeIndex=(await edges.count())-1;edgeIndex>=0;edgeIndex--){
              const candidate=edges.nth(edgeIndex);
              const outsideQuote=await candidate.evaluate(el=>
                !el.closest('.se-component.se-quotation,.se-section-quotation'));
              if(outsideQuote){edge=candidate;break;}
            }
            if(edge){
              const beforeIds=await ctx.evaluate(()=>
                [...document.querySelectorAll('.se-components-wrap .se-component')]
                  .map(el=>el.id));
              const beforeCount=await components.count();
              await edge.scrollIntoViewIfNeeded();
              // 인용구가 문서의 마지막 컴포넌트일 때 이 버튼은 레이아웃상
              // 포인터 판정이 불가능해 일반 click()이 시간 초과된다. 실제
              // SmartEditor 버튼 동작은 유효하므로 강제 클릭해 일반 본문을
              // 인용구 밖에 만든다.
              await edge.click({force:true});
              let afterCount=beforeCount;
              const end=Date.now()+3000;
              while(Date.now()<end){
                await sleep(120);
                afterCount=await components.count();
                if(afterCount>beforeCount)break;
              }
              if(afterCount<=beforeCount){
                // In some quotation states the synthetic pointer click is
                // accepted but SmartEditor does not run its edge handler.
                // A focused native click reliably invokes the same control.
                try{await edge.evaluate(el=>{el.focus();el.click();});}catch(_){}
                const retryEnd=Date.now()+3000;
                while(Date.now()<retryEnd){
                  await sleep(120);
                  afterCount=await components.count();
                  if(afterCount>beforeCount)break;
                }
              }
              let added=null;
              for(let i=afterCount-1;i>=0;i--){
                const candidate=components.nth(i);
                const id=await candidate.getAttribute('id');
                const state=await candidate.evaluate(el=>({
                  isText:el.classList.contains('se-text'),
                  text:(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim()
                }));
                // 보통 새 ID의 빈 텍스트 컴포넌트가 생기지만, 편집기
                // 복원 상태에서는 기존 빈 컴포넌트를 재사용하기도 한다.
                if(state.isText&&!state.text&&(id&&!beforeIds.includes(id)||!added)){
                  added=candidate;
                  if(id&&!beforeIds.includes(id))break;
                }
              }
              if(!added)continue;
              const paragraph=added.locator('.se-text-paragraph');
              const paragraphCount=await paragraph.count();
              if(!paragraphCount)continue;
              if(!(await focusBodyParagraph(paragraph.nth(paragraphCount-1))))continue;
              return true;
            }
          } catch (_) {}
        }
        return false;
      };

      const setHighlight = async enabled => {
        if(!payload.highlightEnabled)return true;
        const button=await findContext(p,'.se-background-color-toolbar-button');
        if(!button)return false;
        await button.loc.click();await sleep(180);
        let choice=null;
        if(!enabled){
          choice=await findContext(p,'.se-color-palette-no-color');
        } else {
          choice=await findContext(p,`.se-color-palette[data-color="${payload.highlightColor.toLowerCase()}"]`);
          if(!choice){
            choice=await findContext(p,'.se-color-palette[data-color="#ffef34"]');
          }
        }
        if(!choice)return false;
        await choice.loc.click();await sleep(120);return true;
      };

      const insertFormattedText = async raw => {
        const parts=(raw||'').split(/(\[\/?(?:BOLD|HIGHLIGHT)\])/g);
        let bold=false,highlight=false;
        for (const part of parts) {
          if (!part) continue;
          if(part==='[BOLD]'||part==='[/BOLD]'){
            if(payload.boldEnabled){
              const button=await findContext(p,'.se-bold-toolbar-button');
              if(!button)return false;
              await button.loc.click();await sleep(100);
            }
            bold=part==='[BOLD]';continue;
          }
          if(part==='[HIGHLIGHT]'||part==='[/HIGHLIGHT]'){
            highlight=part==='[HIGHLIGHT]';
            if(!(await setHighlight(highlight)))return false;
            continue;
          }
          if(!activeParagraph)return false;
          if(!(await isOutsideQuote(activeParagraph)))return false;
          // pressSequentially() drops literal newlines in SmartEditor and used
          // to render CTA labels and URLs as one run-on line.  Shift+Enter
          // creates a visual line break while keeping the same top-level text
          // component, so the article structure remains deterministic.
          const lines=part.split('\n');
          for(let lineIndex=0;lineIndex<lines.length;lineIndex++){
            if(lineIndex)await p.keyboard.press('Shift+Enter');
            if(lines[lineIndex]){
              const typingTarget=await typingTargetFor(activeParagraph);
              const targetIsParagraph=await typingTarget.evaluate(el=>
                el.classList.contains('se-text-paragraph'));
              if(targetIsParagraph){
                if(p.keyboard&&typeof p.keyboard.insertText==='function')await p.keyboard.insertText(lines[lineIndex]);
                else await p.keyboard.type(lines[lineIndex],{delay:20});
              }else{
                await typingTarget.pressSequentially(lines[lineIndex],{delay:20});
              }
            }
          }
        }
        if(bold&&payload.boldEnabled){const button=await findContext(p,'.se-bold-toolbar-button');if(button)await button.loc.click();}
        if(highlight&&payload.highlightEnabled)await setHighlight(false);
        return true;
      };

      // Build the article as stable top-level text/image components first.
      // SmartEditor's quotation widget has a second, provider-owned cite
      // field, so trying to escape it while streaming the article can put the
      // next paragraph into that cite field.  We instead remember the five
      // heading-only text components and convert them in place after the body
      // and images are complete.
      const pendingQuoteHeadings=[];
      const insertPlainQuoteHeading = async rawHeading => {
        const heading=(rawHeading||'').split('\n')[0].trim().slice(0,30);
        if(!heading)return true;
        if(!activeParagraph||!(await isOutsideQuote(activeParagraph))){
          if(!(await focusEnd()))return false;
        }
        const existing=await activeParagraph.evaluate(el=>(el.innerText||el.textContent||'')
          .replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim());
        if(existing&&!(await focusEnd()))return false;
        if(!(await insertFormattedText(heading)))return false;
        pendingQuoteHeadings.push(heading);
        // Keep the provider caret and create the following body paragraph
        // natively.  The later quotation conversion splits only the selected
        // heading paragraph, leaving this body paragraph outside the quote.
        const componentId=await activeParagraph.evaluate(el=>el.closest('.se-component')?.id||'');
        await p.keyboard.press('Enter');await sleep(180);
        let paragraphs=componentId
          ? bodyFound.ctx.locator(`[id="${componentId}"] .se-text-paragraph`)
          : bodyFound.ctx.locator('.se-components-wrap > .se-component.se-text:last-child .se-text-paragraph');
        const paragraphCount=await paragraphs.count();
        if(!paragraphCount)return false;
        activeParagraph=paragraphs.nth(paragraphCount-1);
        if(!(await focusBodyParagraph(activeParagraph)))return false;
        reuseCurrentParagraph=true;
        return true;
      };

      const insertQuote = async heading => {
        heading=(heading||'').split('\n')[0].trim().slice(0,30);
        if (!heading) return true;
        quoteFailureStage='focus-before';
        // Type in a normal top-level paragraph first, then use SmartEditor's
        // quotation toolbar to convert that filled paragraph. Naver's empty
        // quote placeholder intermittently drops all keyboard input.
        reuseCurrentParagraph=false;
        let quoteInputReady=false;
        const topComponents=bodyFound.ctx.locator(
          '.se-components-wrap .se-component.se-text'
        );
        const topCount=await topComponents.count();
        if(topCount){
          const lastComponent=topComponents.nth(topCount-1);
          if(await lastComponent.evaluate(el=>el.classList.contains('se-text'))){
            let paragraphs=lastComponent.locator('.se-text-paragraph');
            let paragraphCount=await paragraphs.count();
            if(paragraphCount){
              let paragraph=paragraphs.nth(paragraphCount-1);
              const lastText=await paragraph.evaluate(el=>(el.innerText||el.textContent||'')
                .replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim());
              if(lastText){
                // insertFormattedText leaves the live provider caret at the
                // end of this final paragraph. Do not refocus its locator—the
                // pointer midpoint would split a wrapped Korean line.
                await p.keyboard.press('Enter');await sleep(300);
                paragraphs=lastComponent.locator('.se-text-paragraph');
                paragraphCount=await paragraphs.count();
                if(!paragraphCount)return false;
                paragraph=paragraphs.nth(paragraphCount-1);
              }
              const readyText=await paragraph.evaluate(el=>(el.innerText||el.textContent||'')
                .replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim());
              if(!readyText&&await focusBodyParagraph(paragraph))quoteInputReady=true;
            }
          }
        }
        if(!quoteInputReady&&!(await focusEnd()))return false;
        if(!(await isOutsideQuote(activeParagraph)))return false;
        const existing=await activeParagraph.evaluate(el=>(el.innerText||el.textContent||'')
          .replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim());
        if(existing)return false;
        let oldQuoteClipboard='';
        try{
          await p.cdp.send('Browser.grantPermissions',{
            origin:'https://cafe.naver.com',
            permissions:['clipboardReadWrite','clipboardSanitizedWrite']
          });
          try{oldQuoteClipboard=await p.evaluate(async()=>await navigator.clipboard.readText());}catch(_){}
          await p.evaluate(async value=>await navigator.clipboard.writeText(value),heading);
        }catch(_){return false;}
        if(!(await focusBodyParagraph(activeParagraph)))return false;
        quoteFailureStage='typing-paste';
        const headingTarget=await typingTargetFor(activeParagraph);
        const targetIsParagraph=await headingTarget.evaluate(el=>el.classList.contains('se-text-paragraph'));
        if(targetIsParagraph){
          quoteFailureStage='typing-paragraph';
          if(p.keyboard&&typeof p.keyboard.insertText==='function')await p.keyboard.insertText(heading);
          else await p.keyboard.type(heading,{delay:20});
        }else{
          await headingTarget.click({force:true});
          await p.keyboard.press('Meta+V');
        }
        await sleep(260);
        let typed=await activeParagraph.evaluate(el=>(el.innerText||el.textContent||'')
          .replace(/내용을 입력하세요\.?/g,'').replace(/\u200b/g,'').trim());
        if(norm(typed)!==norm(heading))return false;
        // Use native keyboard selection so SmartEditor's internal selection
        // model sees the same range as the browser. A DOM Range alone is
        // visually selected but the toolbar treats it as an insert command.
        await focusTextParagraph(activeParagraph);
        await p.keyboard.press('End');
        await p.keyboard.press('Meta+Shift+ArrowLeft');
        const beforeIds=await bodyFound.ctx.evaluate(()=>
          [...document.querySelectorAll('.se-components-wrap > .se-component.se-quotation')]
            .map(el=>el.id));
        // The generic insert button always appends an empty quotation. The
        // quote toolbar converts the currently selected filled paragraph.
        let quoteButton=await findContext(p,'.se-quote-toolbar-button');
        if(!quoteButton){
          quoteButton=await findContext(p,'.se-insert-quotation-default-toolbar-button');
        }
        quoteFailureStage='quote-button';
        if(!quoteButton)return false;
        await quoteButton.loc.click();
        await sleep(180);
        const quoteStyle=await findContext(p,'.se-quote-toolbar-menu li button');
        if(quoteStyle){await quoteStyle.loc.click();await sleep(180);}
        let added=null;
        const end=Date.now()+3500;
        while(Date.now()<end&&!added){
          await sleep(120);
          const quotes=bodyFound.ctx.locator(
            '.se-components-wrap > .se-component.se-quotation'
          );
          for(let i=0;i<await quotes.count();i++){
            const candidate=quotes.nth(i);
            const id=await candidate.getAttribute('id');
            if(id&&!beforeIds.includes(id)){added=candidate;break;}
          }
        }
        quoteFailureStage='converted-quote';
        if(!added)return false;
        await sleep(300);
        // The quote placeholder is provider-focused after conversion. Paste
        // immediately without another click, then restore operator clipboard.
        await p.keyboard.press('Meta+V');
        await sleep(300);
        try{await p.evaluate(async value=>await navigator.clipboard.writeText(value),oldQuoteClipboard);}catch(_){}
        let quoteParagraph=added.locator('.se-quote .se-text-paragraph').first();
        if(!(await quoteParagraph.count())){
          quoteParagraph=added.locator('.se-quotation-content .se-text-paragraph').first();
        }
        if(!(await quoteParagraph.count()))quoteParagraph=added.locator('.se-text-paragraph').first();
        if(!(await quoteParagraph.count()))return false;
        const convertedHeading=await added.evaluate(el=>(el.innerText||el.textContent||'')
          .replace(/출처 입력/g,'').replace(/\u200b/g,'').trim());
        if(norm(convertedHeading)!==norm(heading))return false;
        const quoteId=await added.getAttribute('id');
        quoteFailureStage='escaped-body';
        let escapedParagraph=null;
        const findEscapedParagraph=async()=>{
          // Re-query after every provider mutation. Reusing a pre-mutation
          // locator can point back into the quotation's cite paragraph.
          const candidates=bodyFound.ctx.locator(
            `[id="${quoteId}"] ~ .se-component.se-text`
          );
          for(let i=0;i<await candidates.count();i++){
            const candidate=candidates.nth(i);
            const empty=await candidate.evaluate(el=>
              !(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'')
                .replace(/\u200b/g,'').trim());
            if(!empty)continue;
            const paragraphs=candidate.locator('.se-text-paragraph');
            const count=await paragraphs.count();
            if(count)return paragraphs.nth(count-1);
          }
          const ordered=bodyFound.ctx.locator('.se-components-wrap .se-component');
          let seenQuote=false;
          for(let i=0;i<await ordered.count();i++){
            const candidate=ordered.nth(i);
            const id=await candidate.getAttribute('id');
            if(id===quoteId){seenQuote=true;continue;}
            if(!seenQuote)continue;
            const state=await candidate.evaluate(el=>({
              isText:el.classList.contains('se-text'),
              empty:!(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'')
                .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim()
            }));
            if(!state.isText||!state.empty)continue;
            const paragraphs=candidate.locator('.se-text-paragraph');
            const count=await paragraphs.count();
            if(count)return paragraphs.nth(count-1);
          }
          return null;
        };

        // SmartEditor's bottom-edge control is the provider-native way to
        // create a normal text component after a quotation. Use it first;
        // keyboard navigation is only a fallback because wrapped quote lines
        // make ArrowDown/Enter intermittent.
        try{
          const edges=bodyFound.ctx.locator('.se-canvas-bottom-button');
          let edge=null;
          for(let edgeIndex=(await edges.count())-1;edgeIndex>=0;edgeIndex--){
            const candidate=edges.nth(edgeIndex);
            const outsideQuote=await candidate.evaluate(el=>
              !el.closest('.se-component.se-quotation,.se-section-quotation'));
            if(outsideQuote){edge=candidate;break;}
          }
          if(edge){
            await edge.evaluate(el=>{el.focus();el.click();});
          }
        }catch(_){}
        let escapeEnd=Date.now()+8000;
        while(Date.now()<escapeEnd&&!escapedParagraph){
          await sleep(120);
          escapedParagraph=await findEscapedParagraph();
        }
        if(!escapedParagraph){
          await focusTextParagraph(quoteParagraph);
          await p.keyboard.press('End');
          await p.keyboard.press('ArrowDown');
          await sleep(80);
          await p.keyboard.press('ArrowDown');
          await sleep(80);
          await p.keyboard.press('Enter');
          escapeEnd=Date.now()+3500;
          while(Date.now()<escapeEnd&&!escapedParagraph){
            await sleep(120);
            escapedParagraph=await findEscapedParagraph();
          }
        }
        // If the quotation keyboard escape did not create a body component,
        // use SmartEditor's own bottom-edge control via focusEnd().  This stays
        // inside the provider UI and never mutates editor DOM directly.
        if(!escapedParagraph){
          reuseCurrentParagraph=false;
          if(await focusEnd())escapedParagraph=activeParagraph;
        }
        if(!escapedParagraph)return false;
        if(!(await focusBodyParagraph(escapedParagraph)))return false;
        if(!(await isOutsideQuote(activeParagraph)))return false;
        reuseCurrentParagraph=true;
        quoteFailureStage='';
        return true;
      };

      const findImageUpload = async () => {
        for (const ctx of await contextsFor(p)) {
          try {
            const inputs=ctx.locator('input[type="file"]');
            for(let i=(await inputs.count())-1;i>=0;i--){
              const item=inputs.nth(i);
              const accept=(await item.getAttribute('accept')||'').toLowerCase();
              if(/image|\.jpe?g|\.png|\.gif|\.webp/.test(accept))return {ctx,loc:item};
            }
          } catch (_) {}
        }
        return null;
      };

	      const chunks=payload.body.split('[IMAGE_HERE]');
      let insertedImages=0;
      if(!workflowError)await focusEnd();
      for (let i=0; i<chunks.length; i++) {
        if(workflowError)break;
        const parts=chunks[i].split(/(\[BLOCKQUOTE\][\s\S]*?\[\/BLOCKQUOTE\])/g);
        for(const rawPart of parts){
          const part=(rawPart||'').trim();
          if(!part)continue;
          const quote=part.match(/^\[BLOCKQUOTE\]([\s\S]*?)\[\/BLOCKQUOTE\]$/);
          if(quote){
            if(!(await insertPlainQuoteHeading(quote[1]))){
              const diag=await bodyFound.ctx.evaluate(()=>[...document.querySelectorAll('.se-component')].map(el=>({
                kind:el.classList.contains('se-quotation')?'quote':el.classList.contains('se-text')?'text':el.classList.contains('se-image')?'image':'other',
                text:(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'').replace(/출처 입력/g,'').replace(/\u200b/g,'').trim(),
                editables:[...el.querySelectorAll('[contenteditable]')].map(item=>({
                  tag:item.tagName,classes:item.className,value:item.getAttribute('contenteditable'),
                  role:item.getAttribute('role'),tabindex:item.getAttribute('tabindex')
                })),
                paragraphs:[...el.querySelectorAll('.se-text-paragraph')].map(item=>({
                  tag:item.tagName,classes:item.className,contenteditable:item.getAttribute('contenteditable'),
                  role:item.getAttribute('role'),tabindex:item.getAttribute('tabindex'),
                  parent:{tag:item.parentElement?.tagName,classes:item.parentElement?.className,
                    contenteditable:item.parentElement?.getAttribute('contenteditable')},
                  grandparent:{tag:item.parentElement?.parentElement?.tagName,
                    classes:item.parentElement?.parentElement?.className,
                    contenteditable:item.parentElement?.parentElement?.getAttribute('contenteditable')},
                  html:item.outerHTML.slice(0,1200)
                }))
              })));
              workflowError=`네이버 인용구 소제목 입력에 실패했습니다: ${quote[1].split('\n')[0].trim()} (${JSON.stringify(diag)})`;
              break;
            }
          } else {
            if(!(await focusEnd())){
              workflowError='네이버 인용구 뒤의 일반 본문 영역을 만들지 못했습니다.';
              break;
            }
            if(!(await insertFormattedText(part))){workflowError='네이버 본문 서식 입력에 실패했습니다.';break;}
          }
        }
        if(workflowError)break;
        if (i < chunks.length-1 && i < payload.images.length) {
          await focusEnd();
          const item=payload.images[i];
          const beforeState=await bodyFound.ctx.evaluate(()=>({
            images:document.querySelectorAll('.se-component.se-image,.se-section-image').length,
            ready:[...document.querySelectorAll('.se-component.se-image,.se-section-image')]
              .filter(el=>[...el.querySelectorAll('img[src]')].some(img=>img.complete&&img.naturalWidth>0)).length
          }));
          const imageButton = await findContext(p, '.se-image-toolbar-button, button[aria-label*="사진"], button[aria-label*="이미지"]');
          // A foreground Aside tab otherwise opens macOS's native file dialog,
          // which blocks the headless CDP session.  Register the chooser waiter
          // before clicking so Playwright intercepts it and supplies the image
          // payload without surfacing an OS dialog.
          let chooserPromise=null;
          try{
            if(imageButton&&typeof p.waitForEvent==='function'){
              if(typeof p.prepareForPotentialFileChooser==='function'){
                await p.prepareForPotentialFileChooser();
              }
              chooserPromise=p.waitForEvent('filechooser',{timeout:5000}).catch(()=>null);
            }
          }catch(_){}
          if (imageButton) { await imageButton.loc.click(); await sleep(150); }
          const chooser=chooserPromise ? await chooserPromise : null;
          if(chooser){
            if(item.path)await chooser.setFiles(item.path);
            else await chooser.setFiles([{name:item.name,mimeType:item.mimeType,buffer:Buffer.from(item.base64,'base64')}]);
            uploadDebug.push({via:item.path?'filechooser-path':'filechooser',name:item.name});
          }else{
            const upload = await findImageUpload();
            if (!upload) {
              workflowError = '네이버 사진 전용 파일 입력 요소를 찾지 못했습니다.';
              break;
            }
            if(item.path)await upload.loc.setInputFiles(item.path);
            else await upload.loc.setInputFiles([{name:item.name,mimeType:item.mimeType,buffer:Buffer.from(item.base64,'base64')}]);
            try {
              uploadDebug.push(await upload.loc.evaluate(el=>({
                accept:el.getAttribute('accept')||'',
                name:el.getAttribute('name')||'',
                multiple:!!el.multiple,
                files:Number(el.files?.length||0),
                firstSize:Number(el.files?.[0]?.size||0),
              })));
            } catch (_) {}
          }
          let uploadDone=false;
          const uploadEnd=Date.now()+60000;
          while(Date.now()<uploadEnd){
            await sleep(500);
            const state=await bodyFound.ctx.evaluate(before=>{
              const all=[...document.querySelectorAll('.se-component.se-image,.se-section-image')];
              const added=all.slice(before.images);
              return {
                images:all.length,
                ready:all.filter(el=>[...el.querySelectorAll('img[src]')]
                  .some(img=>img.complete&&img.naturalWidth>0)).length,
                addedUploading:added.some(el=>/전송중\.\.\./.test(el.innerText||'')),
                addedError:added.some(el=>/파일 전송 오류/.test(el.innerText||''))
              };
            },beforeState);
            if(state.addedError){workflowError='네이버 이미지 업로드가 실패했습니다.';break;}
            if(state.images>beforeState.images&&state.ready>beforeState.ready&&!state.addedUploading){uploadDone=true;break;}
          }
          if(workflowError)break;
          if(!uploadDone){
            let domDebug=[];
            for(const ctx of await contextsFor(p)){
              try{domDebug.push(await ctx.evaluate(()=>({
                fileInputs:[...document.querySelectorAll('input[type="file"]')].map(el=>({
                  accept:el.getAttribute('accept')||'',name:el.getAttribute('name')||'',
                  multiple:!!el.multiple,files:Number(el.files?.length||0)
                })),
                imageComponents:document.querySelectorAll('.se-component.se-image,.se-section-image,.se-module-image').length,
                editorImages:document.querySelectorAll('.se-canvas img,.se-component img').length,
                uploading:/전송중\.\.\./.test(document.body?.innerText||''),
                uploadError:/파일 전송 오류/.test(document.body?.innerText||''),
              })));}catch(_){}
            }
            workflowError=`네이버 이미지 업로드 완료를 확인하지 못했습니다. 진단=${JSON.stringify({selected:uploadDebug,dom:domDebug}).slice(0,2400)}`;
            break;
          }
          insertedImages++;
          await focusEnd();
        } else if (i < chunks.length - 1) {
          await focusEnd();
        }
      }

      // At this exact point SmartEditor still owns the live caret at the end
      // of the fully typed CTA. Create both provider link cards now, before
      // quotation post-processing moves focus to earlier wrapped headings.
      // Re-finding the CTA later by pointer coordinates was unsafe on long
      // articles because the final Korean sentence can wrap differently.
      let linksInsertedEarly=false;
      if(!workflowError&&(payload.ctaLinkUrl||payload.sourceUrl)){
        let oldEarlyClipboard='';
        try{
          await p.cdp.send('Browser.grantPermissions',{
            origin:'https://cafe.naver.com',
            permissions:['clipboardReadWrite','clipboardSanitizedWrite']
          });
          try{oldEarlyClipboard=await p.evaluate(async()=>await navigator.clipboard.readText());}catch(_){}
          const earlyCardSelector=url=>/youtu(?:\.be|be\.com)/i.test(url||'')
            ? '.se-oembed,.se-video' : '.se-oglink';
          const pasteEarlyCard=async url=>{
            const selector=earlyCardSelector(url);
            const before=await bodyFound.ctx.locator(selector).count();
            await p.evaluate(async value=>await navigator.clipboard.writeText(value),url);
            await p.keyboard.press('Meta+V');
            const end=Date.now()+30000;
            while(Date.now()<end){
              await sleep(400);
              const cards=await bodyFound.ctx.locator(selector).count();
              const raw=await bodyFound.ctx.evaluate(value=>
                [...document.querySelectorAll('.se-text-paragraph')]
                  .some(el=>(el.textContent||'').replace(/\u200b/g,'').trim()===value),url);
              if(cards>before&&raw)return true;
            }
            return false;
          };
          if(payload.ctaLinkUrl){
            await p.keyboard.press('Shift+Enter');
            await p.keyboard.press('Shift+Enter');
            await sleep(120);
            if(!(await pasteEarlyCard(payload.ctaLinkUrl))){
              workflowError='네이버 패밀리데이 OG 링크 카드를 본문 끝에 만들지 못했습니다.';
            }
            if(!workflowError)await sleep(2000);
          }
          if(!workflowError&&payload.sourceUrl){
            if(!payload.ctaLinkUrl){
              await p.keyboard.press('Shift+Enter');
              await p.keyboard.press('Shift+Enter');
              await sleep(120);
            }
            if(p.keyboard&&typeof p.keyboard.insertText==='function'){
              await p.keyboard.insertText(payload.sourceLabel);
            }else{
              await p.keyboard.type(payload.sourceLabel,{delay:20});
            }
            await p.keyboard.press('Shift+Enter');
            if(!(await pasteEarlyCard(payload.sourceUrl))){
              const earlyDebug=await bodyFound.ctx.evaluate(()=>({
                paragraphs:[...document.querySelectorAll('.se-text-paragraph')]
                  .map(el=>(el.textContent||'').replace(/\u200b/g,'').trim())
                  .filter(Boolean).slice(-12),
                kinds:[...document.querySelectorAll('.se-components-wrap > .se-component')]
                  .slice(-12).map(el=>el.classList.contains('se-text')?'text':
                    el.classList.contains('se-quotation')?'quote':
                    el.classList.contains('se-image')?'image':
                    el.classList.contains('se-oglink')?'oglink':
                    el.classList.contains('se-oembed')||el.classList.contains('se-video')?'embed':'other')
              }));
              workflowError=`네이버 원본 영상 링크 카드를 본문 끝에 만들지 못했습니다. 진단=${JSON.stringify(earlyDebug)}`;
            }
          }
          if(!workflowError)linksInsertedEarly=true;
        }catch(error){
          workflowError=`네이버 본문 끝 링크 카드 생성에 실패했습니다: ${String(error?.message||error)}`;
        }finally{
          try{await p.evaluate(async value=>await navigator.clipboard.writeText(value),oldEarlyClipboard);}catch(_){}
        }
      }

      if(!workflowError&&pendingQuoteHeadings.length){
        let oldQuoteClipboard='';
        try{
          await p.cdp.send('Browser.grantPermissions',{
            origin:'https://cafe.naver.com',
            permissions:['clipboardReadWrite','clipboardSanitizedWrite']
          });
          try{oldQuoteClipboard=await p.evaluate(async()=>await navigator.clipboard.readText());}catch(_){}
          for(const heading of pendingQuoteHeadings){
            let component=null;
            let paragraph=null;
            const texts=bodyFound.ctx.locator('.se-components-wrap > .se-component.se-text');
            for(let index=0;index<await texts.count();index++){
              const candidate=texts.nth(index);
              const candidateParagraphs=candidate.locator('.se-text-paragraph');
              for(let paragraphIndex=0;paragraphIndex<await candidateParagraphs.count();paragraphIndex++){
                const item=candidateParagraphs.nth(paragraphIndex);
                const authored=await item.evaluate(el=>
                  [...el.querySelectorAll('.__se-node')]
                    .map(node=>node.textContent||'').join('')
                    .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim());
                if(norm(authored)===norm(heading)){
                  component=candidate;paragraph=item;break;
                }
              }
              if(component)break;
            }
            if(!component){
              const paragraphDebug=await bodyFound.ctx.evaluate(()=>
                [...document.querySelectorAll('.se-components-wrap > .se-component.se-text .se-text-paragraph')]
                  .map(el=>[...el.querySelectorAll('.__se-node')]
                    .map(node=>node.textContent||'').join('')
                    .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim()));
              workflowError=`네이버 인용구로 바꿀 소제목을 찾지 못했습니다: ${heading} (문단=${JSON.stringify(paragraphDebug)})`;
              break;
            }
            if(!paragraph){workflowError=`네이버 인용구 소제목 문단이 없습니다: ${heading}`;break;}
            if(!(await focusBodyParagraph(paragraph))){
              workflowError=`네이버 인용구 소제목에 포커스하지 못했습니다: ${heading}`;
              break;
            }
            const inputNode=await typingTargetFor(paragraph);
            await inputNode.scrollIntoViewIfNeeded();
            await inputNode.click({force:true});
            await p.keyboard.press('End');
            // Option+Shift+ArrowUp selects to the beginning of the paragraph,
            // including wrapped visual lines. Cmd+Shift+Left only selected a
            // fragment when a Korean heading wrapped in the narrow editor.
            await p.keyboard.press('Alt+Shift+ArrowUp');
            const selectedHeading=await bodyFound.ctx.evaluate(()=>(getSelection()?.toString()||'')
              .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim());
            // SmartEditor mirrors the native selection into its own model and
            // clears window.getSelection() in some restored components.  The
            // converted provider component below is the authoritative check.
            await p.evaluate(async value=>await navigator.clipboard.writeText(value),heading);
            const beforeIds=await bodyFound.ctx.evaluate(() =>
              [...document.querySelectorAll('.se-components-wrap > .se-component.se-quotation')]
                .map(el=>el.id));
            let quoteButton=await findContext(p,'.se-quote-toolbar-button');
            if(!quoteButton)quoteButton=await findContext(p,'.se-insert-quotation-default-toolbar-button');
            if(!quoteButton){workflowError='네이버 인용구 도구 버튼을 찾지 못했습니다.';break;}
            await quoteButton.loc.click();await sleep(180);
            const quoteStyle=await findContext(p,'.se-quote-toolbar-menu li button');
            if(quoteStyle){await quoteStyle.loc.click();await sleep(180);}
            let added=null;
            const conversionEnd=Date.now()+3500;
            while(Date.now()<conversionEnd&&!added){
              await sleep(120);
              const quotes=bodyFound.ctx.locator('.se-components-wrap > .se-component.se-quotation');
              for(let quoteIndex=0;quoteIndex<await quotes.count();quoteIndex++){
                const candidate=quotes.nth(quoteIndex);
                const id=await candidate.getAttribute('id');
                if(id&&!beforeIds.includes(id)){added=candidate;break;}
              }
            }
            if(!added){workflowError=`네이버 인용구 변환을 확인하지 못했습니다: ${heading}`;break;}
            await p.keyboard.press('Meta+V');await sleep(300);
            const converted=await added.evaluate(el=>(el.innerText||el.textContent||'')
              .replace(/출처 입력/g,'').replace(/\u200b/g,'').trim());
            if(norm(converted)!==norm(heading)){
              workflowError=`네이버 인용구 내용이 달라졌습니다: ${heading}`;
              break;
            }
            // On restored SmartEditor components the quotation button inserts
            // the new provider block at the selected paragraph boundary but
            // can leave the original heading text immediately before it. The
            // body is already in the following split text component, so clear
            // only that exact old heading with native Backspace keystrokes.
            let oldHeadingParagraph=null;
            const remainingTextComponents=bodyFound.ctx.locator(
              '.se-components-wrap > .se-component.se-text'
            );
            for(let textIndex=0;textIndex<await remainingTextComponents.count();textIndex++){
              const textParagraphs=remainingTextComponents.nth(textIndex).locator('.se-text-paragraph');
              for(let paragraphIndex=0;paragraphIndex<await textParagraphs.count();paragraphIndex++){
                const item=textParagraphs.nth(paragraphIndex);
                const authored=await item.evaluate(el=>
                  [...el.querySelectorAll('.__se-node')].map(node=>node.textContent||'').join('')
                    .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim());
                if(norm(authored)===norm(heading)){oldHeadingParagraph=item;break;}
              }
              if(oldHeadingParagraph)break;
            }
            if(oldHeadingParagraph){
              const oldHeadingNode=await typingTargetFor(oldHeadingParagraph);
              await oldHeadingNode.scrollIntoViewIfNeeded();
              const box=await oldHeadingNode.boundingBox();
              if(box&&box.width>2&&box.height>2){
                await oldHeadingNode.click({force:true,position:{
                  x:Math.max(1,box.width-2),y:Math.max(1,box.height-2)
                }});
              }else{
                await oldHeadingNode.click({force:true});
              }
              await p.keyboard.press('End');
              for(let characterIndex=0;characterIndex<heading.length;characterIndex++){
                await p.keyboard.press('Backspace');
              }
              await sleep(180);
              const residual=await oldHeadingParagraph.evaluate(el=>
                [...el.querySelectorAll('.__se-node')].map(node=>node.textContent||'').join('')
                  .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim());
              if(residual){
                workflowError=`네이버 인용구 변환 전 소제목이 남았습니다: ${heading} (잔여=${residual})`;
                break;
              }
            }
            activeParagraph=null;
            reuseCurrentParagraph=false;
          }
        }catch(error){
          workflowError=`네이버 인용구 후처리에 실패했습니다: ${String(error?.message||error)}`;
        }finally{
          try{await p.evaluate(async value=>await navigator.clipboard.writeText(value),oldQuoteClipboard);}catch(_){}
        }
      }

      if(!workflowError){
        const applyInlineFormat=async(keyword,kind)=>{
          return await bodyFound.ctx.evaluate(({keyword,kind})=>{
            for(const component of document.querySelectorAll('.se-component.se-text')){
              const walker=document.createTreeWalker(component,NodeFilter.SHOW_TEXT);
              while(walker.nextNode()){
                const node=walker.currentNode;
                const index=(node.textContent||'').indexOf(keyword);
                if(index<0)continue;
                const parent=node.parentElement;
                const alreadyBold=!!parent?.closest('b,strong,[style*="font-weight"]');
                const alreadyHighlight=!!parent?.closest('[style*="background"]');
                if((kind==='bold'&&alreadyBold)||(kind==='highlight'&&alreadyHighlight))return true;
                const range=document.createRange();
                range.setStart(node,index);range.setEnd(node,index+keyword.length);
                const wrapper=document.createElement(kind==='bold'?'b':'span');
                if(kind==='highlight')wrapper.style.backgroundColor='rgb(255, 255, 0)';
                range.surroundContents(wrapper);
                for(const target of [wrapper.closest('.se-module-text'),component,document.body]){
                  target?.dispatchEvent(new InputEvent('input',{bubbles:true,
                    inputType:kind==='bold'?'formatBold':'formatBackColor',data:null}));
                }
                return true;
              }
            }
            return false;
          },{keyword,kind});
        };
        for(const keyword of (payload.boldEnabled ? payload.boldKeywords : [])){
          if(!(await applyInlineFormat(keyword,'bold'))){workflowError=`굵게 적용 대상 누락: ${keyword}`;break;}
        }
        if(!workflowError)for(const keyword of (payload.highlightEnabled ? payload.highlightKeywords : [])){
          if(!(await applyInlineFormat(keyword,'highlight'))){workflowError=`강조 적용 대상 누락: ${keyword}`;break;}
        }
      }

      // SmartEditor creates its rich link card only for a genuine clipboard
      // paste. Sequential typing leaves a blue URL but no OG thumbnail or
      // YouTube preview, so append outbound links after the article structure
      // and wait for the corresponding component to appear.
      if(!workflowError&&!linksInsertedEarly&&(payload.ctaLinkUrl||payload.sourceUrl)){
        let oldClipboard='';
        try{
          await p.cdp.send('Browser.grantPermissions',{
            origin:'https://cafe.naver.com',
            permissions:['clipboardReadWrite','clipboardSanitizedWrite']
          });
          try{oldClipboard=await p.evaluate(async()=>await navigator.clipboard.readText());}catch(_){}

          const cardSelectorFor=url=>/youtu(?:\.be|be\.com)/i.test(url||'')
            ? '.se-oembed,.se-video' : '.se-oglink';
          const countCards=async selector=>await bodyFound.ctx.locator(selector).count();
          let linkFailureDebug=null;
          const prepareFooterParagraph=async()=>{
            const components=bodyFound.ctx.locator('.se-components-wrap > .se-component');
            const count=await components.count();
            if(count){
              const last=components.nth(count-1);
              const state=await last.evaluate(el=>({
                isText:el.classList.contains('se-text'),
                text:(el.innerText||el.textContent||'').replace(/내용을 입력하세요\.?/g,'')
                  .replace(/\u200b/g,'').trim()
              }));
              if(state.isText){
                const paragraphs=last.locator('.se-text-paragraph');
                const paragraphCount=await paragraphs.count();
                if(!paragraphCount)return {ok:false,hasText:false};
                const paragraph=paragraphs.nth(paragraphCount-1);
                if(!state.text){
                  // A freshly expanded OG card leaves SmartEditor's native
                  // caret in this empty following component. Preserve it;
                  // clicking the visible placeholder steals that caret and
                  // makes the next YouTube paste a no-op.
                  activeParagraph=paragraph;
                  return {ok:true,hasText:false};
                }
                if(!(await focusBodyParagraph(paragraph)))return {ok:false,hasText:false};
                // The paragraph wrapper is not always SmartEditor's native
                // editing host.  Collapsing a range on that wrapper can leave
                // the live caret on a wrapped visual line in the middle of the
                // CTA.  Anchor it inside the provider's actual __se-node so
                // link cards are appended after the complete sentence.
                const inputNode=await typingTargetFor(paragraph);
                await inputNode.scrollIntoViewIfNeeded();
                const box=await inputNode.boundingBox();
                if(box&&box.width>2&&box.height>2){
                  // Click the lower-right of the final inline node.  This maps
                  // to the end of its last wrapped visual line; a center click
                  // was splitting Korean CTA text around the link card.
                  await inputNode.click({force:true,position:{
                    x:Math.max(1,box.width-2),y:Math.max(1,box.height-2)
                  }});
                }else{
                  await inputNode.click({force:true});
                }
                await p.keyboard.press('End');
                activeParagraph=paragraph;
                return {ok:true,hasText:state.text.length>0};
              }
            }
            return {ok:await focusEnd(),hasText:false};
          };
          const pasteCard=async(url,prefix='')=>{
            if(!url)return true;
            const prepared=await prepareFooterParagraph();
            if(!prepared.ok||!activeParagraph)return false;
            if(prepared.hasText){
              await p.keyboard.press('Shift+Enter');
              await p.keyboard.press('Shift+Enter');
            }
            if(prefix){
              if(p.keyboard&&typeof p.keyboard.insertText==='function')await p.keyboard.insertText(prefix);
              else await p.keyboard.type(prefix,{delay:20});
              await p.keyboard.press('Shift+Enter');
            }
            const selector=cardSelectorFor(url);
            const before=await countCards(selector);
            await p.evaluate(async value=>await navigator.clipboard.writeText(value),url);
            // Keep this on the page keyboard: the paste replaces the active
            // paragraph locator while the command is in flight.
            await p.keyboard.press('Meta+V');
            // Naver sometimes resolves a YouTube oEmbed noticeably later than
            // a normal OG link, especially after several image uploads.
            const end=Date.now()+30000;
            while(Date.now()<end){
              await sleep(400);
              const cards=await countCards(selector);
              const raw=await bodyFound.ctx.evaluate(value=>
                [...document.querySelectorAll('.se-text-paragraph')]
                  .some(el=>(el.textContent||'').replace(/\u200b/g,'').trim()===value),url);
              if(cards>before&&raw)return true;
            }
            linkFailureDebug=await bodyFound.ctx.evaluate(({url,selector,before})=>({
              url,
              before,
              cards:document.querySelectorAll(selector).length,
              paragraphs:[...document.querySelectorAll('.se-text-paragraph')]
                .map(el=>(el.textContent||'').replace(/\u200b/g,'').trim())
                .filter(Boolean).slice(-12),
              componentKinds:[...document.querySelectorAll('.se-components-wrap > .se-component')]
                .slice(-12).map(el=>el.classList.contains('se-text')?'text':
                  el.classList.contains('se-quotation')?'quote':
                  el.classList.contains('se-image')?'image':
                  el.classList.contains('se-oglink')?'oglink':
                  el.classList.contains('se-oembed')||el.classList.contains('se-video')?'embed':'other')
            }),{url,selector,before});
            return false;
          };

          if(payload.ctaLinkUrl&&!(await pasteCard(payload.ctaLinkUrl))){
            workflowError=`네이버 패밀리데이 OG 링크 카드를 만들지 못했습니다. 진단=${JSON.stringify(linkFailureDebug)}`;
          }
          if(!workflowError&&payload.sourceUrl&&
              !(await pasteCard(payload.sourceUrl,payload.sourceLabel))){
            workflowError=`네이버 원본 영상 링크 카드를 만들지 못했습니다. 진단=${JSON.stringify(linkFailureDebug)}`;
          }
        }catch(error){
          workflowError=`네이버 링크 카드 생성에 실패했습니다: ${String(error?.message||error)}`;
        }finally{
          try{await p.evaluate(async value=>await navigator.clipboard.writeText(value),oldClipboard);}catch(_){}
        }
        if(!workflowError)await sleep(1200);
      }

      const formatState=await bodyFound.ctx.evaluate(({sourceUrl,ctaLinkUrl})=>{
        const components=[...document.querySelectorAll(
          '.se-components-wrap > .se-component'
        )];
        const html=components.map(el=>el.outerHTML||'').join('\n');
        const text=components.map(el=>el.innerText||el.textContent||'').join('\n');
        const paragraphs=[...document.querySelectorAll('.se-text-paragraph')]
          .map(el=>(el.textContent||'').replace(/\u200b/g,'').trim());
        const meaningful=components.map(el=>{
          const clean=(el.innerText||el.textContent||'').replace(/출처 입력/g,'')
            .replace(/내용을 입력하세요\.?/g,'')
            .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim();
          const authored=[...el.querySelectorAll('.se-text-paragraph .__se-node')]
            .map(node=>node.textContent||'').join('\n')
            .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim();
          const visibleText=authored.replace(/\s/g,'');
          const pastedUrl=authored===ctaLinkUrl||authored===sourceUrl;
          if(el.classList.contains('se-quotation'))return {kind:'quote',text:clean};
          if(el.classList.contains('se-image'))return {kind:'image',text:''};
          if(el.classList.contains('se-text')&&visibleText&&!pastedUrl)return {kind:'text',text:authored};
          return null;
        }).filter(Boolean);
        return {quotes:components.filter(el=>el.classList.contains('se-quotation')).length,
          images:components.filter(el=>el.classList.contains('se-image')).length,
          bold:(html.match(/se-style-bold|font-weight\s*:\s*(?:bold|[6-9]00)|<(?:b|strong)\b/gi)||[]).length,
          highlight:(html.match(/background(?:-color)?\s*:/gi)||[]).length,
          source:!sourceUrl||text.includes(sourceUrl),
          ctaLink:!ctaLinkUrl||paragraphs.includes(ctaLinkUrl),
          sourceRaw:!sourceUrl||paragraphs.includes(sourceUrl),
          oglinks:document.querySelectorAll('.se-oglink').length,
          embeds:document.querySelectorAll('.se-oembed,.se-video').length,
          sequence:meaningful.map(item=>item.kind),
          quoteTexts:meaningful.filter(item=>item.kind==='quote')
            .map(item=>item.text.replace(/\u00a0/g,' ')),
          textValues:meaningful.filter(item=>item.kind==='text').map(item=>item.text)};
      },{sourceUrl:payload.sourceUrl,ctaLinkUrl:payload.ctaLinkUrl});
      if(!workflowError&&formatState.quotes!==payload.expectedQuotes)workflowError='네이버 인용구 서식 개수가 원고와 다릅니다.';
      if(!workflowError&&formatState.images<insertedImages)workflowError='네이버 이미지 삽입 개수가 원고와 다릅니다.';
      if(!workflowError&&payload.boldEnabled&&payload.expectedBold&&formatState.bold===0)workflowError='네이버 볼드 서식 적용을 확인하지 못했습니다.';
      if(!workflowError&&payload.sourceUrl&&!formatState.source)workflowError='원본 영상 링크가 본문에 들어가지 않았습니다.';
      if(!workflowError&&payload.ctaLinkUrl&&!formatState.ctaLink)workflowError='패밀리데이 링크 원문이 본문에 들어가지 않았습니다.';
      if(!workflowError&&payload.sourceUrl&&!formatState.sourceRaw)workflowError='원본 영상 URL 원문이 본문에 들어가지 않았습니다.';
      const expectedEmbeds=[payload.ctaLinkUrl,payload.sourceUrl]
        .filter(url=>url&&/youtu(?:\.be|be\.com)/i.test(url)).length;
      const expectedOglinks=[payload.ctaLinkUrl,payload.sourceUrl]
        .filter(url=>url&&!/youtu(?:\.be|be\.com)/i.test(url)).length;
      if(!workflowError&&formatState.embeds<expectedEmbeds)workflowError='YouTube 미리보기 카드 개수가 부족합니다.';
      if(!workflowError&&formatState.oglinks<expectedOglinks)workflowError='OG 링크 카드 개수가 부족합니다.';
      if(!workflowError&&JSON.stringify(formatState.sequence)!==JSON.stringify(payload.expectedSequence)){
        workflowError=`네이버 본문 컴포넌트 순서가 원고와 다릅니다. 기대=${JSON.stringify(payload.expectedSequence)}, 현재=${JSON.stringify(formatState.sequence)}, 텍스트=${JSON.stringify(formatState.textValues)}`;
      }
      if(!workflowError&&JSON.stringify(formatState.quoteTexts)!==JSON.stringify(payload.expectedQuoteTexts)){
        workflowError=`네이버 인용구 내용이 원고와 다릅니다. 현재=${JSON.stringify(formatState.quoteTexts)}`;
      }
      if (workflowError) {
        emit({status:'error', message:workflowError});
      } else if (payload.saveDraft) {
        const saveButton=await findContext(p,'.btn_temp_save');
        if(!saveButton){
          emit({status:'error',message:'네이버 임시등록 버튼을 찾지 못했습니다.'});
        }else{
          await saveButton.loc.click();await sleep(1600);
          const countButton=await findContext(p,'.btn_temp_count');
          if(!countButton){
            emit({status:'error',message:'임시등록 후 임시글 목록 버튼을 찾지 못했습니다.'});
          }else{
            await countButton.loc.click();await sleep(500);
            const saved=await countButton.ctx.evaluate(title=>{
              const wanted=(title||'').replace(/\s+/g,'').trim();
              const matches=[...document.querySelectorAll('.temp_item')].filter(item=>
                (item.querySelector('.temp_item_title')?.textContent||'').replace(/\s+/g,'').trim()===wanted);
              return {count:matches.length,time:(matches[0]?.querySelector('.temp_time')?.textContent||'').trim()};
            },payload.title);
            if(saved.count!==1){
              emit({status:'error',message:`임시등록한 제목을 목록에서 하나로 확인하지 못했습니다: ${saved.count}개`});
            }else{
              const preview=payload.capturePreview ? Buffer.from(await p.screenshot()).toString('base64') : '';
              const url=p.url();const targetId=String(p.targetId||'');
              if(!payload.visibleTabId)await p.close();
              emit({status:'draft_saved',url,target_id:targetId,saved_time:saved.time,
                images:formatState.images,board:selectedBoard,quotes:formatState.quotes,
                bold:formatState.bold,highlight:formatState.highlight,
                source_link:formatState.source,cta_link:formatState.ctaLink,
                oglinks:formatState.oglinks,embeds:formatState.embeds,
                sequence:formatState.sequence,quote_texts:formatState.quoteTexts,
                _preview_png:preview});
            }
          }
        }
      } else if (!payload.publish) {
        let cardsPreview='';
        if(payload.capturePreview){
          if(payload.ctaLinkUrl||payload.sourceUrl){
            try{
              const cards=bodyFound.ctx.locator('.se-oglink,.se-oembed,.se-video');
              const count=await cards.count();
              if(count){
                await cards.nth(count-1).scrollIntoViewIfNeeded();await sleep(300);
                cardsPreview=Buffer.from(await p.screenshot()).toString('base64');
              }
            }catch(_){}
          }
          try{await p.evaluate(()=>window.scrollTo(0,0));await sleep(300);}catch(_){}
        }
        const preview=payload.capturePreview ? Buffer.from(await p.screenshot()).toString('base64') : '';
        emit({status:'filled', url:p.url(), images:formatState.images, board:selectedBoard,
          quotes:formatState.quotes, bold:formatState.bold, highlight:formatState.highlight,
          source_link:formatState.source, cta_link:formatState.ctaLink,
          oglinks:formatState.oglinks, embeds:formatState.embeds,
          target_id:String(p.targetId||''),
          sequence:formatState.sequence, quote_texts:formatState.quoteTexts,
          _preview_png:preview, _preview_cards_png:cardsPreview});
      } else {
        const beforeUrl = p.url();
        const button = await findContext(p, 'a.BaseButton--skinGreen,button.btn_register,button[class*="register"],button[class*="publish"]');
        let clicked = false;
        if (button) { await button.loc.click(); clicked = true; }
        if (!clicked) {
          clicked = await bodyFound.ctx.evaluate(() => {
            const all=[...document.querySelectorAll('button,a,[role=button]')];
            const hit=all.find(el => (el.textContent||'').trim()==='등록');
            if (hit) { hit.click(); return true; } return false;
          });
        }
        if (!clicked) emit({status:'error', message:'네이버 카페 등록 버튼을 찾지 못했습니다.'});
        else {
          let confirmed=false;
          const end=Date.now()+30000;
          while(Date.now()<end){
            await sleep(700);
            const titleStill=await findContext(p,'.textarea_input');
            if(p.url()!==beforeUrl || !titleStill){confirmed=true;break;}
          }
          if(!confirmed){
            let diagnostics={url:p.url(),messages:[]};
            try{diagnostics.messages=await bodyFound.ctx.evaluate(()=>[...document.querySelectorAll(
              '[role="alert"],[role="dialog"],.toast,.popup,.layer_popup,[class*="error"]'
            )].filter(el=>{const s=getComputedStyle(el);const r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;})
              .map(el=>(el.innerText||el.textContent||'').trim()).filter(Boolean).slice(0,5));}catch(_){}
            if(!payload.visibleTabId)await p.close();
            emit({status:'error',message:`등록 버튼 클릭 후 완료 화면을 확인하지 못했습니다. 진단=${JSON.stringify(diagnostics)}`});
          }
          else {
          const url = p.url();
          if(!payload.visibleTabId) await p.close();
          emit({status:'published', url, images:formatState.images, board:selectedBoard,
            quotes:formatState.quotes, bold:formatState.bold, highlight:formatState.highlight,
            source_link:formatState.source, cta_link:formatState.ctaLink,
            oglinks:formatState.oglinks, embeds:formatState.embeds});
          }
        }
      }
    }
  }
}
}
"""
        result = run_repl(code, cwd=upload_dir, timeout=300, account=account)
        return _save_preview(result, preview_path)


def publish_saved_naver_cafe_draft(
    title: str,
    *,
    cafe_url: str,
    board_name: str,
    expected_images: int,
    expected_quotes: int,
    expected_sequence: list[str],
    expected_quote_texts: list[str],
    cta_link_url: str,
    source_url: str,
    account: str | None = None,
    preview_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Open one exact persisted draft, revalidate it, and publish fail-closed."""
    decoded = cafe_url or ""
    club = re.search(r"clubid[=&](\d+)|cafes/(\d+)", decoded, re.I)
    menu = re.search(r"menuid[=&](\d+)|menus/(\d+)|boardId[=&](\d+)", decoded, re.I)
    clubid = next((group for group in club.groups() if group), "") if club else ""
    menuid = next((group for group in menu.groups() if group), "") if menu else ""
    if not clubid or not menuid:
        raise ValueError("네이버 카페 글쓰기 URL에서 카페/게시판 ID를 확인하지 못했습니다.")
    write_url = (
        f"https://cafe.naver.com/ca-fe/cafes/{clubid}/menus/{menuid}/articles/write?boardType=L"
    )
    payload = {
        "title": title.strip(),
        "writeUrl": write_url,
        "boardName": board_name.strip(),
        "expectedImages": int(expected_images),
        "expectedQuotes": int(expected_quotes),
        "expectedSequence": list(expected_sequence),
        "expectedQuoteTexts": list(expected_quote_texts),
        "ctaLinkUrl": cta_link_url.strip(),
        "sourceUrl": source_url.strip(),
        "capturePreview": bool(preview_path),
    }
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const norm=value=>(value||'').replace(/\s+/g,'').trim();
const p=await openTab(payload.writeUrl);
await sleep(2500);
if(await pageLooksLoggedOut(p,'naver')){
  await p.goto('https://nid.naver.com/nidlogin.login');
  emit({status:'login_required',site:'naver'});
}else{
  let titleField=await waitForContext(p,'.textarea_input',15000);
  if(!titleField){
    await p.close();emit({status:'error',message:'네이버 임시글을 열 글쓰기 화면을 찾지 못했습니다.'});
  }else{
    const countButton=await findContext(p,'.btn_temp_count');
    if(!countButton){
      await p.close();emit({status:'error',message:'네이버 임시글 목록 버튼을 찾지 못했습니다.'});
    }else{
      await countButton.loc.click();await sleep(500);
      const matches=countButton.ctx.locator('.temp_item').filter({hasText:payload.title});
      const exact=[];
      for(let i=0;i<await matches.count();i++){
        const item=matches.nth(i);
        const itemTitle=await item.locator('.temp_item_title').textContent();
        if(norm(itemTitle)===norm(payload.title))exact.push(item);
      }
      if(exact.length!==1){
        await p.close();emit({status:'error',message:`정확한 제목의 임시글이 ${exact.length}개라 발행하지 않았습니다.`});
      }else{
        const savedTime=(await exact[0].locator('.temp_time').textContent()||'').trim();
        await exact[0].locator('.temp_link').click();await sleep(1800);
        titleField=await waitForContext(p,'.textarea_input',10000);
        const restoredTitle=titleField ? (await titleField.loc.evaluate(el=>el.value||'')).trim() : '';
        const body=await waitForContext(p,'.se-text-paragraph',10000);
        if(!titleField||!body||norm(restoredTitle)!==norm(payload.title)){
          await p.close();emit({status:'error',message:'선택한 네이버 임시글 제목/본문이 복원되지 않았습니다.'});
        }else{
          let selectedBoard='';
          try{selectedBoard=await titleField.ctx.evaluate(()=>
            (document.querySelector('.FormSelectButton button')?.textContent||'').trim());}catch(_){}
          const state=await body.ctx.evaluate(({sourceUrl,ctaLinkUrl})=>{
            const components=[...document.querySelectorAll('.se-components-wrap > .se-component')];
            const paragraphs=[...document.querySelectorAll('.se-text-paragraph')]
              .map(el=>(el.textContent||'').replace(/\u200b/g,'').trim());
            const meaningful=components.map(el=>{
              const clean=(el.innerText||el.textContent||'').replace(/출처 입력/g,'')
                .replace(/내용을 입력하세요\.?/g,'')
                .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim();
              const authored=[...el.querySelectorAll('.se-text-paragraph .__se-node')]
                .map(node=>node.textContent||'').join('\n')
                .replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim();
              const visibleText=authored.replace(/\s/g,'');
              const pastedUrl=authored===ctaLinkUrl||authored===sourceUrl;
              if(el.classList.contains('se-quotation'))return {kind:'quote',text:clean};
              if(el.classList.contains('se-image'))return {kind:'image',text:''};
              if(el.classList.contains('se-text')&&visibleText&&!pastedUrl)return {kind:'text',text:authored};
              return null;
            }).filter(Boolean);
            return {
              images:components.filter(el=>el.classList.contains('se-image')).length,
              quotes:components.filter(el=>el.classList.contains('se-quotation')).length,
              sequence:meaningful.map(item=>item.kind),
              quoteTexts:meaningful.filter(item=>item.kind==='quote')
                .map(item=>item.text.split('\n')[0].replace(/\u00a0/g,' ').trim().slice(0,30)),
              ctaRaw:!ctaLinkUrl||paragraphs.includes(ctaLinkUrl),
              sourceRaw:!sourceUrl||paragraphs.includes(sourceUrl),
              oglinks:document.querySelectorAll('.se-oglink').length,
              embeds:document.querySelectorAll('.se-oembed,.se-video').length
            };
          },{sourceUrl:payload.sourceUrl,ctaLinkUrl:payload.ctaLinkUrl});
          let error='';
          if(norm(selectedBoard)!==norm(payload.boardName))error=`게시판 불일치: ${selectedBoard||'확인 불가'}`;
          else if(state.images!==payload.expectedImages)error=`이미지 수 불일치: ${state.images}`;
          else if(state.quotes!==payload.expectedQuotes)error=`인용구 수 불일치: ${state.quotes}`;
          else if(JSON.stringify(state.sequence)!==JSON.stringify(payload.expectedSequence))error='본문 컴포넌트 순서 불일치';
          else if(JSON.stringify(state.quoteTexts)!==JSON.stringify(payload.expectedQuoteTexts))error='인용구 제목 불일치';
          else if(!state.ctaRaw||!state.sourceRaw)error='CTA 또는 원본 URL 원문 누락';
          else if(payload.ctaLinkUrl&&state.oglinks<1)error='패밀리데이 OG 카드 누락';
          else if(payload.sourceUrl&&state.embeds<1)error='YouTube 미리보기 카드 누락';
          if(error){
            await p.close();emit({status:'error',message:`네이버 임시글 재검증 실패: ${error}`});
          }else{
            const preview=payload.capturePreview ? Buffer.from(await p.screenshot()).toString('base64') : '';
            const beforeUrl=p.url();
            const register=await findContext(p,'button.btn_register,a.BaseButton--skinGreen,button[class*="register"],button[class*="publish"]');
            let clicked=false;
            if(register){await register.loc.click();clicked=true;}
            if(!clicked){
              clicked=await body.ctx.evaluate(()=>{
                const hit=[...document.querySelectorAll('button,a,[role=button]')]
                  .find(el=>(el.textContent||'').trim()==='등록');
                if(hit){hit.click();return true;}return false;
              });
            }
            if(!clicked){
              await p.close();emit({status:'error',message:'네이버 카페 등록 버튼을 찾지 못했습니다.'});
            }else{
              let confirmed=false;
              const end=Date.now()+30000;
              while(Date.now()<end){
                await sleep(700);
                const editor=await findContext(p,'.textarea_input');
                if(p.url()!==beforeUrl||!editor){confirmed=true;break;}
              }
              if(!confirmed){
                let diagnostics={url:p.url(),messages:[]};
                try{diagnostics.messages=await body.ctx.evaluate(()=>[...document.querySelectorAll(
                  '[role="alert"],[role="dialog"],.toast,.popup,.layer_popup,[class*="error"]'
                )].filter(el=>{const s=getComputedStyle(el);const r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;})
                  .map(el=>(el.innerText||el.textContent||'').trim()).filter(Boolean).slice(0,5));}catch(_){}
                await p.close();emit({status:'error',message:`네이버 카페 등록 완료 화면을 확인하지 못했습니다. 진단=${JSON.stringify(diagnostics)}`});
              }else{
                const url=p.url();await p.close();
                emit({status:'published',url,title:payload.title,board:selectedBoard,saved_time:savedTime,
                  images:state.images,quotes:state.quotes,sequence:state.sequence,
                  quote_texts:state.quoteTexts,oglinks:state.oglinks,embeds:state.embeds,
                  _preview_png:preview});
              }
            }
          }
        }
      }
    }
  }
}
"""
    result = run_repl(code, timeout=180, account=account)
    return _save_preview(result, preview_path)


def fetch_naver_articles(
    board_url: str,
    *,
    page_number: int = 1,
    account: str | None = None,
) -> list[dict[str, str]]:
    """Read non-notice Cafe article ids, titles and visible date strings."""
    payload = {"url": board_url, "page": max(1, int(page_number))}
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
let url = payload.url;
if (payload.page > 1) url += (url.includes('?') ? '&' : '?') + 'page=' + payload.page;
const p = await openTab(url);
await sleep(2500);
if (await pageLooksLoggedOut(p, 'naver')) {
  await p.goto('https://nid.naver.com/nidlogin.login');
  emit({status:'login_required', site:'naver'});
} else {
  let workflowError='';
  let items=[];
  const end=Date.now()+14000;
  while (Date.now()<end) {
    for (const ctx of await contextsFor(p)) {
      try {
        const found=await ctx.evaluate(() => {
          const out=[], seen={};
          for (const a of document.querySelectorAll('a[href*="/articles/"],a[href*="articleid="]')) {
            const tr=a.closest?.('tr'); if(!tr || /board-notice/.test(tr.className||'') || a.closest?.('.board-notice')) continue;
            const m=(a.href||'').match(/articles\/(\d+)/)||(a.href||'').match(/articleid=(\d+)/);
            const title=(a.textContent||'').trim(); if(!m||!title||seen[m[1]]) continue;
            seen[m[1]]=1;
            const cells=[...tr.querySelectorAll('td,.td_date,.date')].map(c=>(c.innerText||'').trim());
            out.push({id:m[1],title,cells});
          }
          return out;
        });
        if (found.length > items.length) items=found;
      } catch (_) {}
    }
    if (items.length) break;
    await sleep(700);
  }
  const finalUrl=p.url(); await p.close();
  emit({status:'ok',url:finalUrl,items});
}
"""
    result = run_repl(code, timeout=45, account=account)
    return list(result.get("items") or [])


def write_naver_comment(
    *,
    clubid: str,
    article_id: str,
    board_url: str,
    text: str,
    dry_run: bool = False,
    signatures: Iterable[str] = (),
    skip_if_commented: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """Inspect or submit one Cafe comment through Aside."""
    modern = "/f-e/" in board_url or "/cafes/" in board_url
    url = (
        f"https://cafe.naver.com/f-e/cafes/{clubid}/articles/{article_id}?fromList=true"
        if modern
        else f"https://cafe.naver.com/ArticleRead.nhn?clubid={clubid}&articleid={article_id}"
    )
    payload = {
        "url": url,
        "text": text,
        "dryRun": bool(dry_run),
        "signatures": list(signatures),
        "skip": bool(skip_if_commented),
        "articleId": str(article_id),
    }
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const p=await openTab(payload.url); await sleep(2500);
if (await pageLooksLoggedOut(p,'naver')) {
  await p.goto('https://nid.naver.com/nidlogin.login'); emit({status:'login_required',site:'naver'});
} else {
  let target=await waitForContext(p,'textarea.comment_inbox_text, textarea[placeholder*="댓글"]',12000);
  if (!target) {
    const u=p.url(); await p.close(); emit({status:'ok',ok:false,reason:'comment_box_not_found',url:u});
  } else {
    const isNotice=await target.ctx.evaluate(()=>!!document.querySelector('.badge_notice,.title_notice'));
    if (isNotice) { await p.close(); emit({status:'ok',ok:false,reason:'notice'}); }
    else {
      const existing=await target.ctx.evaluate(() => {
        const sel='.comment_text_view,.comment_text_box,.comment_list_area,li[class*=CommentItem],[class*=comment]';
        return [...document.querySelectorAll(sel)].map(e=>e.innerText||'').join('\n');
      });
      if (payload.skip && payload.signatures.some(s=>existing.includes(s))) {
        await p.close(); emit({status:'ok',ok:true,reason:'already_commented'});
      } else {
        const button=await findContext(p,'a.btn_register,.btn_register,button.btn_register');
        const hasButton=!!button;
        if (payload.dryRun) {
          await p.close(); emit({status:'ok',ok:hasButton,reason:hasButton?'ready':'register_button_not_found'});
        } else if (!hasButton) {
          await p.close(); emit({status:'ok',ok:false,reason:'register_button_not_found'});
        } else {
          await target.loc.fill(payload.text);
          await button.loc.click();
          let confirmed=false;
          const end=Date.now()+12000;
          while(Date.now()<end){
            await sleep(600);
            const value=await target.loc.inputValue().catch(()=>null);
            const comments=await target.ctx.evaluate(()=>{
              const sel='.comment_text_view,.comment_text_box,.comment_list_area,li[class*=CommentItem]';
              return [...document.querySelectorAll(sel)].map(e=>e.innerText||'').join('\n');
            });
            if(value===''||comments.includes(payload.text)){confirmed=true;break;}
          }
          const u=p.url(); await p.close();
          emit({status:'ok',ok:confirmed,reason:confirmed?'submitted_confirmed':'submit_unconfirmed',url:u});
        }
      }
    }
  }
}
"""
    return run_repl(code, timeout=60, account=account)


def fetch_naver_stats(clubid: str, date_prefix: str, *, account: str | None = None) -> dict[str, Any]:
    """Read Cafe visitor/view totals and top articles for one date."""
    payload = {"clubid": str(clubid), "date": date_prefix}
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
async function bodyText(url) {
  const p=await openTab(url); await sleep(2800);
  if (await pageLooksLoggedOut(p,'naver')) return {p,text:'',loggedOut:true};
  const text=await p.evaluate(()=>document.body?.innerText||''); return {p,text,loggedOut:false};
}
const base='https://cafe.stat.naver.com/cafe/'+payload.clubid;
const uv=await bodyText(base+'/visit/uv');
if (uv.loggedOut) { await uv.p.goto('https://nid.naver.com/nidlogin.login'); emit({status:'login_required',site:'naver'}); }
else {
  const cv=await bodyText(base+'/visit/cv'); const rank=await bodyText(base+'/rank/article');
  const daily=text => {
    for (const line of text.split('\n')) if (line.trim().startsWith(payload.date)) {
      const nums=line.slice(line.indexOf(payload.date)+payload.date.length).match(/[\d,]+/g)||[];
      if(nums.length) return Number(nums[0].replaceAll(',',''));
    } return null;
  };
  const top=[];
  for (const line of rank.text.split('\n')) {
    const parts=line.split('\t').map(x=>x.trim());
    if(parts.length>=6 && /^\d+$/.test(parts[0])) {
      top.push([Number(parts[0]),parts[1],parts[2],Number((parts.at(-1)||'').replace(/\D/g,''))||0]);
      if(top.length>=3) break;
    }
  }
  await uv.p.close(); await cv.p.close(); await rank.p.close();
  emit({status:'ok',visitors:daily(uv.text),views:daily(cv.text),top});
}
"""
    return run_repl(code, timeout=90, account=account)


def extract_naver_members(
    member_url: str,
    *,
    inspect: bool = False,
    max_pages: int = 400,
    account: str | None = None,
) -> dict[str, Any]:
    """Read Cafe manager member rows, optionally walking pagination."""
    payload = {"url": member_url, "inspect": bool(inspect), "maxPages": int(max_pages)}
    code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const p=await openTab(payload.url); await sleep(2600);
if (await pageLooksLoggedOut(p,'naver')) {
  await p.goto('https://nid.naver.com/nidlogin.login'); emit({status:'login_required',site:'naver'});
} else {
  const ctx=(await contextsFor(p)).find(async x=>await x.locator('table tr').count()) || p;
  const chooseCtx=async()=>{
    for(const c of await contextsFor(p)) if(await c.locator('table tr').count()) return c;
    return p;
  };
  const members={}, samples=[], joinDates=[]; let pageNo=1, iframe=false;
  while(pageNo<=payload.maxPages){
    const c=await chooseCtx(); iframe=c!==p;
    const rows=await c.evaluate(()=>[...document.querySelectorAll('table tr')].map(tr=>(tr.innerText||'').replace(/\s+/g,' ').trim()).filter(Boolean));
    for(const row of rows){
      if(samples.length<12) samples.push(row.slice(0,160));
      const m=row.match(/^(.+?)\s*\(([A-Za-z0-9_-]{2,40})\)/);
      if(m){
        if(!members[m[2]]) members[m[2]]=m[1].trim();
        const d=row.match(/(\d{4})\.(\d{2})\.(\d{2})\./);
        if(d) joinDates.push(`${d[1]}-${d[2]}-${d[3]}`);
      }
    }
    if(payload.inspect) break;
    const move=await c.evaluate(target=>{
      const box=document.querySelector('#paginate'); if(!box)return 'end';
      for(const a of box.querySelectorAll('a')) if((a.textContent||'').trim()===String(target)){a.click();return 'num';}
      const next=box.querySelector('a.next'); if(next){next.click();return 'next';} return 'end';
    },pageNo+1);
    if(move==='end') break;
    await sleep(900); pageNo++;
  }
  const finalUrl=p.url(); await p.close();
  emit({status:'ok',members,join_dates:joinDates,rows:Object.keys(members).length,pages:pageNo,iframe,samples});
}
"""
    return run_repl(code, timeout=max(90, min(900, max_pages * 4)), account=account)


def post_to_youtube_community(
    post_text: str,
    image_paths: Iterable[str | os.PathLike[str]],
    *,
    publish: bool = False,
    community_url: str = "",
    expected_channel: str = "",
    preview_path: str | os.PathLike[str] | None = None,
    account: str | None = None,
    visible_tab_id: str = "",
) -> dict[str, Any]:
    """Fill or publish a YouTube Community post using Aside, not Playwright."""
    with staged_uploads(list(image_paths)[:10]) as (upload_dir, upload_names):
        payload = {
            "text": post_text,
            "images": _embedded_uploads(upload_dir, upload_names),
            "publish": bool(publish),
            "url": community_url or "https://studio.youtube.com/",
            "expected": expected_channel.strip(),
            "capturePreview": bool(preview_path and not publish),
            "visibleTabId": visible_tab_id.strip(),
        }
        code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
let p=null;
if(payload.visibleTabId){
  try{p=await attachBrowserTab(payload.visibleTabId);}
  catch(_){emit({status:'error',message:'현재 보이는 YouTube 탭에 연결하지 못했습니다.'});}
}else p=await openTab(payload.url);
if(p) await sleep(2600);
if(!p){
  // The attach failure above already emitted a machine-readable result.
}
else
if (await pageLooksLoggedOut(p,'youtube')) {
  emit({status:'login_required',site:'youtube'});
} else {
  let workflowError='';
  if(!payload.url.includes('posts')&&!payload.url.includes('community')){
    let href=await p.evaluate(()=>{
      const links=[...document.querySelectorAll('a[href]')];
      const hit=links.find(a=>/show_create_dialog=1/.test(a.href));
      return hit?.href||'';
    });
    if(!href){
      const channel=(p.url().match(/\/channel\/([^/?]+)/)||[])[1];
      if(channel)href=`https://www.youtube.com/channel/${channel}/posts?show_create_dialog=1`;
    }
    if(href){p=await openTab(href);await sleep(2200);}
  }
  if(payload.expected){
    const visibleText=await p.evaluate(()=>[document.title,document.body?.innerText||'',
      ...[...document.querySelectorAll('[aria-label],[title]')].map(e=>(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||''))].join('\n'));
    if(!visibleText.includes(payload.expected)) workflowError='활성 YouTube 채널을 확인하지 못했습니다.';
  }
  let box=workflowError ? null : await waitForContext(
    p,'#contenteditable-root[contenteditable="true"],[contenteditable="true"][aria-label*="공유해 보세요"]',16000
  );
  if(!box){
    await p.evaluate(()=>{
      const all=[...document.querySelectorAll('button,[role=button]')].filter(x=>{
        const r=x.getBoundingClientRect(),s=getComputedStyle(x);
        return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
      });
      const b=all.find(x=>x.id==='commentbox-placeholder') ||
        all.find(x=>x.matches?.('button.ytd-backstage-post-dialog-renderer')) ||
        all.find(x=>/어떤 생각을|새로운 소식을|공유해 보세요|무엇을|게시물 작성/.test((x.innerText||x.getAttribute('aria-label')||'')));
      if(b)b.click();
    });
    await sleep(500); box=await waitForContext(
      p,'#contenteditable-root[contenteditable="true"],[contenteditable="true"][aria-label*="공유해 보세요"]',8000
    );
  }
  if(workflowError) emit({status:'error',message:workflowError});
	  else if(!box) emit({status:'error',message:'YouTube 게시글 입력창을 찾지 못했습니다.'});
	  else {
	    const tag=await box.loc.evaluate(el=>el.tagName).catch(()=> '');
	    if(tag==='TEXTAREA'||tag==='INPUT') await box.loc.fill(payload.text);
	    else {
	      await box.loc.click();
	      await p.keyboard.press('Meta+A');
	      await p.keyboard.press('Backspace');
	      await box.ctx.evaluate(value=>document.execCommand('insertText',false,value),payload.text);
	    }
	    let attachedImages=0;
	    let maxImages=0;
	    let imagePreviewVisible=payload.images.length===0;
	    if(payload.images.length){
	      const imageMode=await p.evaluate(()=>{
	        const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
	        const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
	        const b=[...(root?.querySelectorAll('button,[role=button]')||[])].find(x=>{
	          const label=(x.innerText||x.getAttribute('aria-label')||'').trim();
	          return /^(이미지|사진)$/.test(label)&&vis(x);
	        });
	        if(!b)return false;b.click();return true;
	      });
	      if(!imageMode)workflowError='YouTube 이미지 모드 버튼을 찾지 못했습니다.';
	      await sleep(700);
	      let input=null,fallbackInput=null;
	      if(!workflowError){
	        for(const ctx of await contextsFor(p)){
	          try{
	            const inputs=ctx.locator('ytd-backstage-post-dialog-renderer[is-open] input[type="file"][name="Filedata"][multiple]');
	            for(let i=0;i<await inputs.count();i++){
	              const item=inputs.nth(i);fallbackInput={ctx,loc:item};
	              const active=await item.evaluate(el=>{
	                const owner=el.closest('ytd-backstage-multi-image-select-renderer,ytd-backstage-image-select-renderer,tp-yt-paper-dialog');
	                if(!owner)return false;
	                const r=owner.getBoundingClientRect(),s=getComputedStyle(owner);
	                return !owner.hidden&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
	              });
	              if(active){input={ctx,loc:item};break;}
	            }
	          }catch(_){}
	          if(input)break;
	        }
	        input=input||fallbackInput;
	      }
	      if(!input) workflowError='YouTube 이미지 파일 입력 요소를 찾지 못했습니다.';
	      else {
        const files=payload.images.map(item=>({name:item.name,mimeType:item.mimeType,buffer:Buffer.from(item.base64,'base64')}));
        await input.loc.setInputFiles(files);
        const end=Date.now()+30000;
        while(Date.now()<end){
          const state=await p.evaluate(()=>{
            const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
            if(!root)return {count:0,max:0};
	            const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
	            const multi=[...root.querySelectorAll('ytd-backstage-multi-image-select-renderer')]
	              .find(el=>!el.hidden&&el.showImagesPreview&&(vis(el)||[...el.querySelectorAll('img')].some(vis)));
	            return {
	              count:Number(multi?.images?.length||0),
	              max:Number(multi?.getAttribute('max-num-images-per-post')||0),
	              visual:!!multi&&[...multi.querySelectorAll('img')].some(vis)
	            };
	          });
	          attachedImages=state.count;maxImages=state.max;imagePreviewVisible=state.visual;
	          if(attachedImages===payload.images.length&&state.visual)break;
          await sleep(500);
        }
        if(maxImages&&payload.images.length>maxImages){
          workflowError=`YouTube 이미지 제한(${maxImages}장)을 넘었습니다.`;
	        }else if(attachedImages!==payload.images.length){
	          workflowError=`YouTube 이미지 첨부 확인 실패: 요청 ${payload.images.length}장, 확인 ${attachedImages}장`;
	        }else if(!imagePreviewVisible){
	          workflowError='YouTube 카드뉴스 미리보기가 화면에 표시되지 않았습니다.';
	        }
      }
    }
    const postReady=workflowError ? false : await p.evaluate(()=>{
      const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
      if(!root)return false;
      const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
      const button=[...root.querySelectorAll('button,[role=button]')].find(x=>
        (x.innerText||x.getAttribute('aria-label')||'').trim()==='게시'&&vis(x)
      );
      return !!button&&!button.disabled&&button.getAttribute('aria-disabled')!=='true';
    });
	    if(!workflowError&&!postReady) workflowError='YouTube 게시 버튼이 활성화되지 않았습니다.';
	    if(workflowError) emit({status:'error',message:workflowError});
	    else if(!payload.publish){
	      let previewBytes=null,cardsBytes=null,previewScope='',previewDebug={};
	      if(payload.capturePreview){
	        try{
	          const cardsDebug=await p.evaluate(()=>{
	            const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
	            const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
	            const target=[...(root?.querySelectorAll('ytd-backstage-multi-image-select-renderer')||[])]
	              .find(el=>!el.hidden&&el.showImagesPreview&&(vis(el)||[...el.querySelectorAll('img')].some(vis)));
	            if(!target)return {cardsFound:false};
	            for(let node=target.parentElement;node&&node!==document.documentElement;node=node.parentElement){
	              if(node.scrollHeight>node.clientHeight+4){
	                const nr=node.getBoundingClientRect(),tr=target.getBoundingClientRect();
	                node.scrollTop+=tr.top-nr.top-(node.clientHeight/2);
	              }
	            }
	            target.scrollIntoView({block:'center',inline:'nearest'});
	            const r=target.getBoundingClientRect();
	            return {cardsFound:true,cardsTop:Math.round(r.top),cardsBottom:Math.round(r.bottom)};
	          });
	          await sleep(400);
	          if(cardsDebug.cardsFound){cardsBytes=await p.screenshot();previewDebug.cards=cardsDebug;}
	        }catch(_){}
	        try{
	          previewDebug.button=await p.evaluate(()=>{
	            const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
	            const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
	            const button=[...(root?.querySelectorAll('button,[role=button]')||[])].find(x=>
	              (x.innerText||x.getAttribute('aria-label')||'').trim()==='게시'&&vis(x)
	            );
	            if(!button)return {found:false};
	            const brief=r=>({top:Math.round(r.top),bottom:Math.round(r.bottom),height:Math.round(r.height)});
	            const before=brief(button.getBoundingClientRect());
	            const changed=[];
	            for(let node=button.parentElement;node&&node!==document.documentElement;node=node.parentElement){
	              if(node.scrollHeight>node.clientHeight+4){
	                const nr=node.getBoundingClientRect(),br=button.getBoundingClientRect();
	                const prior=node.scrollTop;
	                node.scrollTop+=br.top-nr.top-(node.clientHeight/2);
	                if(node.scrollTop!==prior)changed.push(node.tagName.toLowerCase());
	              }
	            }
	            const scroller=document.scrollingElement;
	            if(scroller){
	              const br=button.getBoundingClientRect();
	              scroller.scrollTop+=br.top-(window.innerHeight/2);
	            }
	            button.scrollIntoView({block:'center',inline:'nearest'});
	            return {found:true,before,after:brief(button.getBoundingClientRect()),changed};
	          });
	          await sleep(500);
	        }catch(_){}
	        previewBytes=await p.screenshot();previewScope='post_button';
	      }
	      const preview=previewBytes ? Buffer.from(previewBytes).toString('base64') : '';
	      const cardsPreview=cardsBytes ? Buffer.from(cardsBytes).toString('base64') : '';
	      emit({status:'filled',url:p.url(),images:attachedImages,post_ready:true,
	        preview_scope:previewScope,preview_debug:previewDebug,
	        _preview_png:preview,_preview_cards_png:cardsPreview});
    }
    else {
      const all=await contextsFor(p); let clicked=false;
      for(const c of all) if(!clicked) clicked=await c.evaluate(()=>{
        const root=document.querySelector('ytd-backstage-post-dialog-renderer[is-open]');
        const b=[...(root?.querySelectorAll('button,[role=button]')||[])].find(x=>(x.innerText||x.getAttribute('aria-label')||'').trim()==='게시');
        if(b&&!b.disabled&&b.getAttribute('aria-disabled')!=='true'){b.click();return true;} return false;
      });
      if(!clicked) emit({status:'error',message:'활성화된 YouTube 게시 버튼을 찾지 못했습니다.'});
      else {
        let confirmed=false;
        const end=Date.now()+15000;
        while(Date.now()<end){
          await sleep(700);
          let composerHasText=false, successText=false;
          for(const c of await contextsFor(p)){
            try{
              const state=await c.evaluate(text=>({
                composer:[...document.querySelectorAll('[contenteditable="true"],textarea')].some(e=>(e.innerText||e.value||'').includes(text.slice(0,30))),
                success:/게시물이 (?:생성|게시)|Post published/i.test(document.body?.innerText||'')
              }),payload.text);
              composerHasText ||= state.composer; successText ||= state.success;
            }catch(_){}
          }
          if(successText||!composerHasText){confirmed=true;break;}
        }
        if(!confirmed) emit({status:'error',message:'YouTube 게시 클릭 후 완료 상태를 확인하지 못했습니다.'});
        else {const url=p.url();if(!payload.visibleTabId)await p.close();emit({status:'published',url,images:attachedImages});}
      }
    }
  }
}
"""
        result = run_repl(code, cwd=upload_dir, timeout=300, account=account)
        return _save_preview(result, preview_path)


def upload_youtube_short(
    video_path: str | os.PathLike[str],
    title: str,
    description: str = "",
    *,
    publish: bool = True,
    expected_channel: str = "",
    account: str | None = None,
) -> dict[str, Any]:
    """Upload a verified vertical MP4 in YouTube Studio through Aside CLI.

    A provider URL/video id is required before a publishing call can report
    success.  The upload is supplied as an in-memory FilePayload because Aside
    currently exposes path-based uploads to the page as zero-byte files.
    """
    path = Path(video_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(str(path))
    # Run the production gate before staging a file or opening Studio.  MP4
    # dimensions alone do not prove the voice, PIP, source footage, subtitles,
    # or mix are the approved ones.
    from content_production_policy import ASIDE_ACCOUNT
    from shorts_video import normalize_shorts_title, validate_upload_ready

    validate_upload_ready(path)
    clean_title = normalize_shorts_title(title)
    if publish and not expected_channel.strip():
        raise ValueError("게시 전 오발행 방지를 위해 expected_channel이 필요합니다.")
    selected_account = account or ASIDE_ACCOUNT
    if selected_account != ASIDE_ACCOUNT:
        raise ValueError("YouTube 발행은 검증된 Aside u0 세션만 사용합니다.")

    with staged_uploads([path]) as (upload_dir, upload_names):
        payload = {
            "video": _embedded_uploads(upload_dir, upload_names)[0],
            "title": clean_title[:100],
            "description": (description or "").strip()[:5000],
            "publish": bool(publish),
            "expected": expected_channel.strip(),
        }
        code = JS_COMMON + f"\nconst payload = {_payload_expression(payload)};\n" + r"""
const p=await openTab('https://studio.youtube.com/');
await sleep(3000);
if(await pageLooksLoggedOut(p,'youtube')){
  emit({status:'login_required',site:'youtube'});
}else{
  let workflowError='';
  if(payload.expected){
    const channelText=await p.evaluate(()=>[
      document.title,document.body?.innerText||'',
      ...[...document.querySelectorAll('[aria-label],[title]')]
        .map(e=>(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||''))
    ].join('\n'));
    if(!channelText.includes(payload.expected))workflowError='활성 YouTube 채널을 확인하지 못했습니다.';
  }
  if(!workflowError){
    const opened=await p.evaluate(()=>{
      const vis=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
      const buttons=[...document.querySelectorAll('button,[role="button"],ytcp-button')];
      const button=buttons.find(x=>vis(x)&&(x.id==='upload-icon'||/동영상 업로드|Upload videos/i.test(x.getAttribute('aria-label')||x.innerText||'')));
      if(!button)return false;button.click();return true;
    });
    if(!opened)workflowError='YouTube Studio 동영상 업로드 버튼을 찾지 못했습니다.';
  }
  await sleep(900);
  let fileInput=null;
  if(!workflowError){
    const inputEnd=Date.now()+12000;
    while(Date.now()<inputEnd&&!fileInput){
      fileInput=await findAnyContext(p,'input[type="file"][name="Filedata"],input[type="file"]');
      if(!fileInput)await sleep(350);
    }
    if(!fileInput)workflowError='YouTube 동영상 파일 입력 요소를 찾지 못했습니다.';
  }
  if(!workflowError){
    const item=payload.video;
    await fileInput.loc.setInputFiles([{name:item.name,mimeType:item.mimeType,buffer:Buffer.from(item.base64,'base64')}]);
  }
  const titleBox=workflowError?null:await waitForContext(
    p,'#title-textarea #textbox,[aria-label*="제목"][contenteditable="true"],ytcp-social-suggestions-textbox#title-textarea [contenteditable="true"]',90000
  );
  if(!workflowError&&!titleBox)workflowError='YouTube 동영상 제목 입력창을 찾지 못했습니다.';
  const fillEditable=async(found,value)=>{
    if(!found)return false;
    await found.loc.click();
    await p.keyboard.press('Meta+A');
    await p.keyboard.press('Backspace');
    await found.loc.pressSequentially(value,{delay:1});
    const landed=(await found.loc.innerText()).trim();
    // YouTube's contenteditable normalizes pasted line breaks and may insert
    // non-breaking/zero-width spaces around links and hashtags.  Keep the
    // value check strict for every visible character while ignoring only that
    // editor-owned whitespace representation.
    const compact=text=>(text||'')
      .replace(/[\u200B-\u200D\uFEFF]/g,'')
      .replace(/\u00A0/g,' ')
      .replace(/\r\n?/g,'\n')
      .replace(/\s+/g,'');
    return compact(landed)===compact(value);
  };
  if(!workflowError){
    if(!(await fillEditable(titleBox,payload.title)))workflowError='YouTube 제목 입력값 검증에 실패했습니다.';
    const descriptionBox=await waitForContext(
      p,'#description-textarea #textbox,[aria-label*="설명"][contenteditable="true"],ytcp-social-suggestions-textbox#description-textarea [contenteditable="true"]',12000
    );
    if(!workflowError&&descriptionBox&&payload.description&&!(await fillEditable(descriptionBox,payload.description))){
      workflowError='YouTube 설명 입력값 검증에 실패했습니다.';
    }
    // The current YouTube Studio upload UI hides audience settings in its
    // second details container. Expand that container before locating the
    // "not made for kids" control, while remaining compatible with the older UI.
    const secondContainerExpand=p.locator('#second-container-expand-button').first();
    if(!workflowError&&await secondContainerExpand.count()){
      await secondContainerExpand.click();
      await sleep(500);
    }
    const noKids=await waitForContext(
      p,
      'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], [role="radio"][name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
      8000
    );
    if(noKids){
      await noKids.loc.click();
      await sleep(350);
      if((await noKids.loc.getAttribute('aria-checked'))!=='true'){
        workflowError='YouTube 아동용 아님 선택을 확인하지 못했습니다.';
      }
    }else{
      workflowError='YouTube 아동용 아님 옵션을 찾지 못했습니다.';
    }
  }
  const clickNext=async()=>{
    const end=Date.now()+90000;
    while(Date.now()<end){
      let clicked=false;
      const buttons=p.locator('#next-button');
      if(await buttons.count()){
        const b=buttons.first();
        const disabled=(await b.getAttribute('aria-disabled'))==='true'||!(await b.isEnabled());
        if(!disabled){
          try{await b.click();clicked=true;}catch(_){}
        }
      }
      if(clicked){await sleep(900);return true;}
      await sleep(650);
    }
    return false;
  };
  if(!workflowError){
    for(let i=0;i<3;i++)if(!(await clickNext())){workflowError=`YouTube 업로드 ${i+1}번째 다음 버튼이 활성화되지 않았습니다.`;break;}
  }
  if(!workflowError){
    const publicRadio=p.locator('tp-yt-paper-radio-button[name="PUBLIC"]');
    if(!(await publicRadio.count()))workflowError='YouTube 공개 공개범위 옵션을 찾지 못했습니다.';
    else{
      await publicRadio.click();await sleep(500);
      if((await publicRadio.getAttribute('aria-checked'))!=='true'){
        workflowError='YouTube 공개 공개범위 선택을 확인하지 못했습니다.';
      }
    }
  }
  if(workflowError){
    emit({status:'error',message:workflowError});
  }else if(!payload.publish){
    emit({status:'filled',url:p.url(),upload_ready:true});
  }else{
    let clicked=false;
    const end=Date.now()+180000;
    while(Date.now()<end&&!clicked){
      const buttons=p.locator('#done-button');
      if(await buttons.count()){
        const b=buttons.first();
        const disabled=(await b.getAttribute('aria-disabled'))==='true'||!(await b.isEnabled());
        if(!disabled){
          try{await b.click();clicked=true;}catch(_){}
        }
      }
      if(!clicked)await sleep(900);
    }
    if(!clicked){
      emit({status:'error',message:'YouTube 게시 버튼이 활성화되지 않았습니다.'});
    }else{
      let videoId='',providerUrl='',publishedRow='';
      const confirmEnd=Date.now()+180000;
      while(Date.now()<confirmEnd&&!publishedRow){
        await sleep(900);
        const state=await p.evaluate(title=>{
          const text=document.body?.innerText||'';
          const row=[...document.querySelectorAll('ytcp-video-row')]
            .find(item=>(item.innerText||'').includes(title));
          return {
            text:text.slice(-5000),
            row:row?.innerText||'',
            hrefs:[...document.querySelectorAll('a[href]')].map(a=>a.href)
              .filter(h=>/youtu\.be|youtube\.com\/(?:watch|shorts)|studio\.youtube\.com\/video\//.test(h))
          };
        },payload.title);
        for(const href of state.hrefs){
          const match=href.match(/(?:youtu\.be\/|[?&]v=|\/shorts\/|\/video\/)([A-Za-z0-9_-]{11})/);
          if(match){videoId=match[1];providerUrl=`https://www.youtube.com/shorts/${videoId}`;break;}
        }
        if(state.row.includes('공개')&&state.row.includes('게시됨'))publishedRow=state.row;
      }
      if(!videoId||!publishedRow){
        emit({status:'error',message:'YouTube 게시 후 공개/게시됨 행과 동영상 ID를 확인하지 못했습니다.'});
      }else{
        await p.close();
        emit({status:'published',video_id:videoId,url:providerUrl,confirmed:true});
      }
    }
  }
}
"""
        result = run_repl(code, cwd=upload_dir, timeout=720, account=selected_account)
        if publish and (
            result.get("status") != "published" or not result.get("video_id")
        ):
            raise AsideError(f"YouTube Shorts 게시 확인 실패: {result}")
        return result
