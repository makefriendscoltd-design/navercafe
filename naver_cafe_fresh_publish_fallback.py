"""Fresh-editor fallback for one already-validated Cafe queue item.

This is used only after the persisted-draft publisher fails for a provider-state
reason. It rebuilds the same manifest/body/images in a fresh Aside u0 editor,
verifies the public page, and records CRM only after provider confirmation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from aside_browser import (
    JS_COMMON,
    _naver_structure_expectation,
    _payload_expression,
    _save_preview,
    post_to_naver_cafe,
    run_repl,
)
from external_publish_tracking import track_external_event
from naver_cafe_draft_scheduler import _load_manifest, _provider_lock, _write_result


CTA_TEXT = (
    "AI 자동화를 직접 배우는 오프라인 스터디를 진행하고 있습니다. "
    "관심 있으시면 아래 패밀리데이 모집 안내 글을 읽어보세요."
)


def _ids(cafe_url: str) -> tuple[str, str]:
    club = re.search(r"clubid[=&](\d+)|cafes/(\d+)", cafe_url, re.I)
    menu = re.search(r"menuid[=&](\d+)|menus/(\d+)|boardId[=&](\d+)", cafe_url, re.I)
    club_id = next((value for value in club.groups() if value), "") if club else ""
    menu_id = next((value for value in menu.groups() if value), "") if menu else ""
    if not club_id or not menu_id:
        raise ValueError("manifest 카페 URL에서 club/menu ID를 확인하지 못했습니다.")
    return club_id, menu_id


def _local_inputs(manifest_path: Path, manifest: dict[str, Any]) -> tuple[Path, list[Path], str]:
    cafe_dir = manifest_path.resolve().parent
    body_path = next(
        (
            path
            for path in (
                cafe_dir / "03_cafe_body.txt",
                cafe_dir / "09_cafe_body_for_provider.txt",
            )
            if path.is_file()
        ),
        None,
    )
    if body_path is None:
        raise FileNotFoundError(f"정본 카페 본문이 없습니다: {cafe_dir}")
    body = body_path.read_text(encoding="utf-8").strip()
    image_sets = [
        sorted((cafe_dir / "youtube_frames").glob("youtube-frame-*.jpg")),
        sorted((cafe_dir / "images").glob("youtube-frame-*.jpg")),
    ]
    images = next((items for items in image_sets if items), [])
    if len(images) != int(manifest["expected_images"]):
        raise ValueError(f"정본 이미지 수가 다릅니다: {len(images)}")
    sequence, quotes = _naver_structure_expectation(body)
    expected_sequence = list(manifest["expected_sequence"])
    # The two URL-card footer paragraphs are appended by post_to_naver_cafe.
    if manifest.get("cta_link_url") and (not sequence or sequence[-1] != "text"):
        sequence.append("text")
    if manifest.get("source_url") and (
        manifest.get("cta_link_url") or not sequence or sequence[-1] != "text"
    ):
        sequence.append("text")
    if sequence != expected_sequence:
        raise ValueError(f"본문 컴포넌트 순서가 manifest와 다릅니다: {sequence}")
    if quotes != list(manifest["expected_quote_texts"]):
        raise ValueError("본문 인용구 제목이 manifest와 다릅니다.")
    if len(quotes) != int(manifest["expected_quotes"]):
        raise ValueError(f"본문 인용구 수가 다릅니다: {len(quotes)}")
    return body_path, images, body


def _find_public_exact(title: str, board_url: str, account: str) -> list[dict[str, str]]:
    payload = {"title": title, "url": board_url}
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r"""
const norm=value=>(value||'').replace(/\s+/g,'').trim();
const wanted=norm(payload.title);const p=await openTab(payload.url);await sleep(3500);
if(await pageLooksLoggedOut(p,'naver')){await p.close();emit({status:'login_required',site:'naver'});}
else{const hits=[];for(const ctx of await contextsFor(p)){try{
  const found=await ctx.evaluate(wanted=>[...document.querySelectorAll('a[href*="/articles/"],a[href*="ArticleRead"]')]
    .map(a=>({title:(a.textContent||'').trim(),url:a.href||''}))
    .filter(x=>(x.title||'').replace(/\s+/g,'').trim()===wanted),wanted);
  for(const item of found)if(!hits.some(x=>x.url===item.url))hits.push(item);
}catch(_){}}await p.close();emit({status:'ok',hits});}
"""
    return list(run_repl(code, timeout=90, account=account).get("hits") or [])


def _verify_public(
    provider_url: str,
    manifest: dict[str, Any],
    account: str,
    preview_path: Path,
) -> dict[str, Any]:
    payload = {
        "url": provider_url,
        "title": manifest["title"],
        "quoteTexts": list(manifest["expected_quote_texts"]),
        "images": int(manifest["expected_images"]),
        "quotes": int(manifest["expected_quotes"]),
        "ctaUrl": manifest["cta_link_url"],
        "sourceUrl": manifest["source_url"],
        "ctaText": CTA_TEXT,
        "sourceLabel": "▶ 원본 영상",
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r"""
const norm=value=>(value||'').replace(/\s+/g,'').trim();
const p=await openTab(payload.url);await sleep(5000);let best=null;
for(const ctx of await contextsFor(p)){try{const state=await ctx.evaluate(payload=>{
  const clean=value=>(value||'').replace(/[\u200B-\u200D\u2060\uFEFF]/g,'').trim();
  const squash=value=>clean(value).replace(/\s+/g,'');
  const root=document.querySelector('.se-main-container,.se-viewer,.article_viewer,.ContentRenderer');
  const article=clean(root?.innerText||root?.textContent||'');
  const components=[...(root||document).querySelectorAll('.se-component')];
  const title=clean(document.querySelector('.ArticleTitle h3,.title_text,[class*="ArticleTitle"]')?.textContent||'');
  const quoteTexts=[...(root||document).querySelectorAll('.se-quotation')]
    .map(el=>clean(el.innerText||el.textContent||'').replace(/출처 입력/g,'').split('\n')[0].trim().slice(0,30));
  return {title,article,articleLength:article.length,quoteTexts,
    images:(root||document).querySelectorAll('.se-image').length,
    quotes:(root||document).querySelectorAll('.se-quotation').length,
    oglinks:(root||document).querySelectorAll('.se-oglink').length,
    embeds:(root||document).querySelectorAll('.se-oembed,.se-video').length,
    bodyEntirelyQuotation:components.length>0&&components.every(el=>el.classList.contains('se-quotation')),
    ctaText:squash(article).includes(squash(payload.ctaText)),ctaRaw:article.includes(payload.ctaUrl),
    sourceLabel:squash(article).includes(squash(payload.sourceLabel)),sourceRaw:article.includes(payload.sourceUrl)};
},payload);if(!best||state.articleLength>best.articleLength)best=state;}catch(_){}}
const verified=!!best&&norm(best.title).endsWith(norm(payload.title))&&best.images===payload.images&&
  best.quotes===payload.quotes&&best.oglinks===1&&best.embeds===1&&
  JSON.stringify(best.quoteTexts)===JSON.stringify(payload.quoteTexts)&&!best.bodyEntirelyQuotation&&
  best.ctaText&&best.ctaRaw&&best.sourceLabel&&best.sourceRaw;
const preview=Buffer.from(await p.screenshot()).toString('base64');const url=p.url();await p.close();
emit({status:'ok',verified,url,...best,_preview_png:preview});
"""
    result = run_repl(code, timeout=120, account=account)
    return _save_preview(result, preview_path)


def run(manifest_path: Path, result_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    body_path, images, body = _local_inputs(manifest_path, manifest)
    club_id, menu_id = _ids(str(manifest["cafe_url"]))
    board_url = f"https://cafe.naver.com/f-e/cafes/{club_id}/menus/{menu_id}?viewType=L"
    account = str(manifest.get("aside_account") or "").strip()
    if account != "u0":
        raise ValueError("Aside account는 u0여야 합니다.")
    local = {
        "body_path": str(body_path),
        "image_count": len(images),
        "title": manifest["title"],
        "board": manifest["board_name"],
    }
    if dry_run:
        return {"status": "dry_run_passed", "local_validation": local}

    hits = _find_public_exact(str(manifest["title"]), board_url, account)
    if len(hits) > 1:
        raise RuntimeError(f"동일 제목 공개 글이 {len(hits)}개라 중단했습니다.")
    if len(hits) == 1:
        provider = {"status": "published", "url": hits[0]["url"], "existing": True}
    else:
        publish_error: Exception | None = None
        try:
            with _provider_lock():
                provider = post_to_naver_cafe(
                    str(manifest["title"]),
                    body,
                    images,
                    cafe_url=board_url,
                    cta_text=CTA_TEXT,
                    cta_link_url=str(manifest["cta_link_url"]),
                    source_label="▶ 원본 영상",
                    source_url=str(manifest["source_url"]),
                    board_name=str(manifest["board_name"]),
                    bold_enabled=True,
                    highlight_enabled=True,
                    publish=True,
                    save_draft=False,
                    account=account,
                )
        except Exception as exc:
            # Naver sometimes keeps the editor URL after accepting the post.  A
            # failed navigation confirmation is not proof that publication
            # failed, so recover only from one exact provider-side title hit.
            publish_error = exc
            provider = {}

        if provider.get("status") != "published" or not provider.get("url"):
            recovered_hits: list[dict[str, str]] = []
            for attempt in range(1, 4):
                recovered_hits = _find_public_exact(
                    str(manifest["title"]), board_url, account
                )
                if recovered_hits:
                    break
                if attempt < 3:
                    time.sleep(2)
            if len(recovered_hits) > 1:
                raise RuntimeError(
                    f"등록 직후 동일 제목 공개 글이 {len(recovered_hits)}개라 중단했습니다."
                )
            if len(recovered_hits) == 1:
                provider = {
                    "status": "published",
                    "url": recovered_hits[0]["url"],
                    "existing": False,
                    "confirmation": "exact_board_lookup_after_click",
                }
            elif publish_error is not None:
                raise publish_error
    if provider.get("status") != "published" or "cafe.naver.com" not in str(provider.get("url") or ""):
        raise RuntimeError("네이버 공급자 발행 URL을 확인하지 못했습니다.")

    public_validation: dict[str, Any] | None = None
    verify_error = ""
    for attempt in range(1, 4):
        try:
            public_validation = _verify_public(
                str(provider["url"]), manifest, account,
                result_path.with_name(f"{result_path.stem}-public.png"),
            )
            if public_validation.get("verified"):
                break
        except Exception as exc:  # provider URL is preserved; never republish here
            verify_error = str(exc)
        if attempt < 3:
            time.sleep(2)
    if not public_validation or not public_validation.get("verified"):
        result = {
            "status": "provider_published_verification_pending",
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": provider,
            "public_validation": public_validation,
            "verification_error": verify_error,
            "crm_tracked": False,
            "local_validation": local,
        }
        _write_result(result_path, result)
        return result

    tracked = False
    if not provider.get("existing"):
        tracked = track_external_event(
            "naver_cafe",
            str(manifest["source_key"]),
            campaign="youtube-content-repurpose",
            stage="sent",
            count=1,
        )
    result = {
        "status": "published",
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": provider,
        "public_validation": public_validation,
        "crm_tracked": tracked,
        "local_validation": local,
    }
    _write_result(result_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.manifest, args.result, dry_run=args.dry_run)
    except Exception as exc:
        result = {
            "status": "blocked",
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": str(exc).strip() or type(exc).__name__,
        }
        if not args.dry_run:
            _write_result(args.result, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"published", "dry_run_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
