"""Publish one validated canonical Cafe manifest through Aside u0.

This module is tracked source code. Runtime queue state and provider evidence
remain under ``outputs/`` and are intentionally not versioned.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parent
LOCK = Path("/tmp/aimax-aside-u0-provider.lock")
EXPECTED_CATEGORY = "AI 자동화&수익화 정보"
EXPECTED_CTA_TEXT = "AI 자동화를 직접 배우는 오프라인 스터디를 진행하고 있습니다. 관심 있으시면 아래 패밀리데이 모집 안내 글을 읽어보세요."
EXPECTED_CTA_URL = "https://cafe.naver.com/westudyssat/4188"
EXPECTED_SOURCE_LABEL = "▶ 원본 영상"
EXPECTED_CAFE_SLUG = "westudyssat"
QUEUE_POLICY_PATH = Path("outputs/cafe-publish-queue-20260823/queue.json")


class CafePublishWindowClosed(RuntimeError):
    """A normal queue throttle, distinct from a broken eligibility gate."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
sys.path.insert(0, str(PROJECT))

from aside_browser import JS_COMMON, _payload_expression, post_to_naver_cafe, run_repl
from content_production_policy import ProductionPolicyError, validate_longform_source


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_long_source_url(manifest: dict) -> str:
    """Read the canonical long YouTube URL while retaining legacy manifests."""
    return str(manifest.get("source_url_long") or manifest.get("source_long_url") or "")


def canonical_cafe_article_url(raw_url: str) -> tuple[str, str]:
    """Turn Naver's iframe redirect URL into a stable, query-free article URL."""
    decoded = raw_url or ""
    article_id = None
    for _ in range(4):
        match = re.search(r"(?:articleid=|/articles/|/westudyssat/)(\d+)", decoded, re.I)
        if match:
            article_id = match.group(1)
            break
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    if not article_id:
        path = urlparse(raw_url or "").path
        match = re.search(r"/(\d+)/?$", path)
        article_id = match.group(1) if match else None
    if not article_id:
        raise RuntimeError("verified Naver Cafe URL has no article id")
    return f"https://cafe.naver.com/{EXPECTED_CAFE_SLUG}/{article_id}", article_id


def enforce_cafe_publish_window(now: datetime | None = None, *, source_key: str | None = None) -> None:
    """Enforce the queue's daily cap and gap again inside the provider lock."""
    queue = read_json(PROJECT / QUEUE_POLICY_PATH)
    timezone = ZoneInfo(queue["timezone"])
    current = (now or datetime.now(timezone)).astimezone(timezone)
    verified_by_source: dict[str, datetime] = {}
    for entry in queue.get("entries", []):
        evidence_source_key = entry.get("source_key")
        evidence_raw = entry.get("provider_evidence")
        if not evidence_source_key or not evidence_raw:
            continue
        evidence_path = PROJECT / evidence_raw
        candidates = [evidence_path, evidence_path.with_name("12_provider_success_reservation.json")]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = read_json(candidate)
                raw_time = payload.get("verifiedAt")
                if payload.get("status") not in ("published_verified", "provider_success_reserved") or not raw_time:
                    continue
                verified_at = datetime.fromisoformat(raw_time).astimezone(timezone)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            previous = verified_by_source.get(evidence_source_key)
            if previous is None or verified_at > previous:
                verified_by_source[evidence_source_key] = verified_at

    today = [stamp for stamp in verified_by_source.values() if stamp.date() == current.date()]
    maximum = int(queue["maximum_successes_per_day"])
    minimum_gap = float(queue["minimum_gap_hours"])
    if len(today) >= maximum:
        raise CafePublishWindowClosed(
            "daily_success_cap",
            f"Cafe daily publish cap reached: {len(today)}/{maximum}",
        )
    if today and (current - max(today)).total_seconds() < minimum_gap * 3600:
        # A user-requested catch-up is bound to one reviewed manifest and expires.
        # It never raises the daily cap or changes the normal scheduler policy.
        for entry in queue.get("entries", []):
            if not source_key or entry.get("source_key") != source_key:
                continue
            authorization = entry.get("immediate_publish_authorization") or {}
            try:
                expires = datetime.fromisoformat(authorization["expires_at"])
                manifest_path = PROJECT / entry["manifest"]
                authorized = (
                    authorization.get("scope") == "waive_minimum_gap_once"
                    and authorization.get("source") == "explicit_user_request"
                    and authorization.get("source_key") == source_key
                    and authorization.get("manifest_sha256") == sha256(manifest_path)
                    and expires.tzinfo is not None and current < expires
                    and entry.get("status") == "pending"
                    and not entry.get("published_url") and not entry.get("do_not_retry")
                )
            except (KeyError, OSError, TypeError, ValueError):
                authorized = False
            if authorized:
                return
        raise CafePublishWindowClosed(
            "minimum_success_gap",
            f"Cafe publish gap has not reached {minimum_gap:g} hours",
        )


def resolve_manifest(raw: str) -> tuple[Path, Path, Path, Path]:
    manifest_path = (PROJECT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    outputs_root = (PROJECT / "outputs").resolve()
    if outputs_root not in manifest_path.parents or manifest_path.name != "06_cafe_manifest.json":
        raise RuntimeError("manifest must be an existing outputs/<source>/cafe/06_cafe_manifest.json")
    if not manifest_path.is_file() or manifest_path.parent.name != "cafe":
        raise RuntimeError("canonical Cafe manifest is missing")
    base = manifest_path.parent.parent
    provider = base / "provider"
    evidence = manifest_path.parent / "provider"
    return manifest_path, base, provider, evidence


def _resolve_cafe_relative(manifest_path: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else manifest_path.parent / candidate


def validate_notebooklm_cafe_provenance(manifest_path: Path, manifest: dict, cafe_local: dict) -> dict:
    source_key = str(manifest.get("source_key") or "")
    long_url = f"https://www.youtube.com/watch?v={source_key}"
    notebook = manifest.get("notebooklm") if isinstance(manifest.get("notebooklm"), dict) else {}
    answer_path = _resolve_cafe_relative(
        manifest_path,
        manifest.get("notebook_answer")
        or manifest.get("notebooklm_answer")
        or notebook.get("answer")
        or "notebooklm/notebooklm-answer.md",
    )
    answer_present = bool(
        answer_path
        and answer_path.is_file()
        and answer_path.read_text(encoding="utf-8").strip()
    )

    canonical_path = _resolve_cafe_relative(manifest_path, manifest.get("notebooklm_provider_evidence")
                                          or "notebooklm/notebooklm-provider-evidence.json")
    canonical = read_json(canonical_path) if canonical_path.is_file() else {}
    canonical_source = str(
        canonical.get("sourceUrl")
        or canonical.get("source_url")
        or canonical.get("targetSourceUrl")
        or ""
    )
    canonical_exact = bool(
        canonical
        and canonical.get("account") == "u0"
        and canonical.get("notebookTitle") == "민수대표님_카페글"
        and canonical_source in {long_url, f"https://youtu.be/{source_key}"}
        and canonical.get("status") in {"ok", "pass", "ok_recovered_after_cli_timeout",
                                       "response_verified_cleanup_pending"}
        and (not canonical.get("answer_sha256") or
             (answer_present and sha256(answer_path) == canonical["answer_sha256"]))
    )

    recovery_candidates = [
        _resolve_cafe_relative(manifest_path, manifest.get("notebooklm_recovery_evidence")),
        manifest_path.parent / "notebooklm_evidence/notebooklm-interrupted-answer-recovery.json",
        manifest_path.parent / "notebooklm-evidence/interrupted-source-recovery-cleanup.json",
    ]
    recovery_exact = False
    for recovery_path in recovery_candidates:
        if not recovery_path or not recovery_path.is_file():
            continue
        recovery = read_json(recovery_path)
        if (
            recovery.get("account") == "u0"
            and recovery.get("notebookTitle") == "민수대표님_카페글"
            and recovery.get("bindingOk") is True
            and recovery.get("answerDone") is True
            and int(recovery.get("answerChars") or 0) > 0
            and (
                cafe_local.get("checks", {}).get("notebook_answer_recovered") is True
                or cafe_local.get("checks", {}).get("notebook_recovery_exact") is True
            )
        ):
            recovery_exact = True
            break

    return {
        "answer_present": answer_present,
        "provider_evidence_exact": canonical_exact or recovery_exact,
    }


def measure_source_video(source_key: str) -> dict:
    """Read the source's real duration and frame size from YouTube."""
    import yt_dlp
    from youtube_source_options import source_options

    options = {**source_options(), "skip_download": True}
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(f"https://youtu.be/{source_key}", download=False)
    return {"seconds": int(info.get("duration") or 0), "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0), "title": str(info.get("title") or "")}


def validate_cafe_eligibility(manifest_path: Path, provider: Path, evidence: Path) -> dict:
    manifest = read_json(manifest_path)
    cafe_local = read_json(manifest_path.parent / "11_local_validation.json")
    approval = read_json(provider / "07_approval_validation.json")
    launch = read_json(provider / "08_launch_consistency_gate.json")
    local_gate = read_json(provider / "09_cafe_only_local_gate.json")
    source_key = manifest.get("source_key")
    source_url = f"https://youtu.be/{source_key}"
    source_long_url = f"https://www.youtube.com/watch?v={source_key}"
    tail = manifest.get("tail", {})
    notebooklm = validate_notebooklm_cafe_provenance(manifest_path, manifest, cafe_local)
    from content_lineage import cafe_body_lineage
    answer_path = _resolve_cafe_relative(
        manifest_path, manifest.get("notebook_answer") or manifest.get("notebooklm_answer")
        or (manifest.get("notebooklm") or {}).get("answer") or "notebooklm/notebooklm-answer.md"
    )
    body_path = _resolve_cafe_relative(manifest_path, manifest.get("body_file"))
    try:
        lineage = cafe_body_lineage(answer_path, body_path) if body_path else {}
        body_preserved = lineage.get("body_preserved") is True
    except (OSError, ValueError):
        body_preserved = False
    checks = {
        "canonical_manifest_exact": approval.get("sourceOfTruth") == str(manifest_path.relative_to(PROJECT)),
        "canonical_manifest_sha256": approval.get("manifestSha256") == sha256(manifest_path),
        "source_key_exact": source_key == approval.get("sourceKey"),
        "source_url_exact": manifest.get("source_url") == source_url,
        "tail_source_url_exact": tail.get("source_url") == source_url,
        "source_long_url_exact": manifest_long_source_url(manifest) == source_long_url,
        "tail_source_long_url_exact": tail.get("source_long_url") == source_long_url,
        "category_exact": manifest.get("category") == EXPECTED_CATEGORY,
        "cta_text_exact": tail.get("cta_text") == EXPECTED_CTA_TEXT,
        "cta_url_exact": tail.get("family_day_url") == EXPECTED_CTA_URL,
        "source_label_exact": tail.get("source_label") == EXPECTED_SOURCE_LABEL,
        "quote_count_exact": manifest.get("expected_quotes") == 5 and len(manifest.get("expected_quote_texts", [])) == 5,
        "image_count_exact": manifest.get("expected_images") == 5 and len(manifest.get("images", [])) == 5,
        "image_files_present": len(manifest.get("images", [])) == 5 and all(
            isinstance(relative, str) and (manifest_path.parent / relative).is_file()
            for relative in manifest.get("images", [])
        ),
        "cafe_local_validation_pass": cafe_local.get("status") == "pass" and cafe_local.get("source_key") == source_key,
        "notebooklm_answer_present": notebooklm["answer_present"],
        "notebooklm_provider_evidence_exact": notebooklm["provider_evidence_exact"],
        "notebooklm_actual_body_preserved": body_preserved,
        "approval_gate_pass": approval.get("status") == "pass" and not approval.get("failures"),
        "launch_consistency_pass": launch.get("status") == "pass" and launch.get("summary", {}).get("issues") == 0,
        "launch_manifest_sha256": launch.get("manifestSha256") == sha256(manifest_path),
        "cafe_only_local_gate_pass": local_gate.get("status") == "pass" and local_gate.get("scope") == "cafe_only",
        "local_gate_manifest_sha256": local_gate.get("manifestSha256") == sha256(manifest_path),
        "local_gate_validation_sha256": local_gate.get("localValidationSha256") == sha256(manifest_path.parent / "11_local_validation.json"),
        "provider_editor_not_opened": manifest.get("provider_editor_opened") is False,
        "provider_mutation_absent": manifest.get("provider_mutation") is False,
        "provider_evidence_absent": not (evidence / "13_provider_evidence.json").exists(),
        "crm_evidence_absent": not (evidence / "14_crm_evidence.json").exists(),
    }
    # Measured live: no stored field can vouch for a source nobody ever measured,
    # and every entry queued before this gate existed lacks one.
    try:
        source_shape = measure_source_video(source_key)
        longform = validate_longform_source(source_shape)
        checks["source_measurement_available"] = True
        checks["source_is_longform"] = True
    except ProductionPolicyError as exc:
        longform = {"error": str(exc), **source_shape}
        checks["source_measurement_available"] = True
        checks["source_is_longform"] = False
    except Exception as exc:
        longform = {"error": f"측정 실패: {exc}"}
        checks["source_measurement_available"] = False
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "sourceVideo": longform,
        "schemaVersion": "cafe-eligibility-gate/v2",
        "status": "pass" if not failures else "fail",
        "scope": "cafe_only",
        "sourceKey": source_key,
        "checks": checks,
        "failures": failures,
        "runtimeRequirements": {
            "fresh_u0_login": True,
            "duplicate_zero_by": ["exact_title", "source_key", "short_source_url", "long_source_url"],
            "public_provider_url_before_crm": True,
            "family_day_og_card": True,
            "youtube_preview_card": True,
        },
        "crossChannelMutationAllowed": {"shorts": False, "community": False},
    }


def crm_emit(source_key: str, evidence_path: Path) -> dict:
    command = [
        "/Users/apple/orca/projects/aimax-crm-observability/bin/aimax-crm-track", "emit",
        "--source-system", "naver_cafe", "--channel", "naver_cafe",
        "--stage", "sent", "--status", "success", "--project", "navercafe",
        "--campaign", f"youtube-repurpose-{source_key}", "--count", "1",
        "--evidence-path", str(evidence_path),
        "--dedupe-key", f"provider-finalization-{source_key}-cafe",
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    return {
        "exitCode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "privacySafe": True,
    }


def publish(manifest_path: Path, base: Path, provider: Path, evidence: Path) -> None:
    eligibility = validate_cafe_eligibility(manifest_path, provider, evidence)
    if eligibility["status"] != "pass":
        raise RuntimeError({"cafe_eligibility_gate_failed": eligibility})
    evidence.mkdir(parents=True, exist_ok=True)
    for uncertain_name in ("provider_uncertain_do_not_retry.json", "published_but_verification_failed_do_not_retry.json"):
        if (evidence / uncertain_name).exists():
            raise RuntimeError(f"{uncertain_name} exists; provider mutation is blocked pending read-only reconciliation")
    evidence_path = evidence / "13_provider_evidence.json"
    if evidence_path.exists():
        raise RuntimeError("Cafe provider evidence already exists; refusing a second provider action")

    manifest = read_json(manifest_path)
    source_key = manifest["source_key"]
    short_url = manifest["source_url"]
    long_url = manifest_long_source_url(manifest)
    body = (manifest_path.parent / manifest["body_file"]).read_text(encoding="utf-8").strip()
    images = [manifest_path.parent / relative for relative in manifest["images"]]
    if len(images) != 5 or not all(path.is_file() for path in images):
        raise RuntimeError("canonical manifest must resolve to exactly five Cafe images")

    payload = {
        "boardUrl": "https://cafe.naver.com/f-e/cafes/26321967/menus/163?viewType=L",
        "searchUrls": [
            "https://cafe.naver.com/ArticleSearchList.nhn?search.clubid=26321967&search.menuid=163&search.searchdate=all&search.searchBy=0&search.query=" + quote_plus(manifest["title"]),
            "https://cafe.naver.com/ArticleSearchList.nhn?search.clubid=26321967&search.menuid=163&search.searchdate=all&search.searchBy=0&search.query=" + quote_plus(source_key),
        ],
        "board": EXPECTED_CATEGORY,
        "title": manifest["title"],
        "sourceKey": source_key,
        "sourceUrls": [short_url, long_url],
    }
    precheck_code = JS_COMMON + "\nconst payload=" + _payload_expression(payload) + ";\n" + r'''
const norm=s=>(s||'').replace(/\s+/g,'').trim();
const collect=async p=>{await sleep(2500);for(let i=0;i<25;i++){const body=await p.locator('body').innerText();if(!body.includes('로딩중...'))break;await sleep(600);}return p.evaluate(()=>{const seen=new Set(),rows=[];for(const a of [...document.querySelectorAll('a[href]')]){const m=(a.href||'').match(/articles\/(\d+)|articleid=(\d+)/i),id=m&&(m[1]||m[2]);if(!id||seen.has(id))continue;seen.add(id);let node=a,best=a;for(let i=0;i<8&&node;i++,node=node.parentElement){const t=(node.innerText||'').trim();if(t&&t.length<1600)best=node;}rows.push({articleId:id,title:(a.innerText||a.textContent||'').trim(),url:a.href,text:(best.innerText||'').trim().slice(0,1600)});}return {url:location.href,body:(document.body?.innerText||'').slice(0,30000),rows};});};
const board=await openTab(`${payload.boardUrl}&cafe_mutation_precheck=${Date.now()}`);const boardState=await collect(board);if(/nid\.naver\.com|로그인/.test(boardState.url)&&!boardState.body.includes(payload.board)){await board.close();emit({status:'login_required'});}else{const searches=[];for(const url of payload.searchUrls){const page=await openTab(`${url}&cafe_mutation_precheck=${Date.now()}`);searches.push(await collect(page));await page.close();}const merged=[...boardState.rows,...searches.flatMap(x=>x.rows)],unique=[];for(const row of merged)if(!unique.some(x=>x.articleId===row.articleId))unique.push(row);const candidates=unique.filter(row=>norm(row.title)===norm(payload.title)||row.text.includes(payload.sourceKey)||payload.sourceUrls.some(sourceUrl=>row.text.includes(sourceUrl)));const inspected=[];for(const row of candidates.slice(0,30)){const page=await openTab(row.url);await sleep(1800);const state=await page.evaluate(()=>({url:location.href,body:(document.body?.innerText||'').slice(0,50000),html:(document.body?.innerHTML||'').slice(0,160000)}));inspected.push({articleId:row.articleId,rowTitle:row.title,url:state.url,exactTitle:norm(row.title)===norm(payload.title),sourceKey:state.body.includes(payload.sourceKey)||state.html.includes(payload.sourceKey),sourceUrl:payload.sourceUrls.some(sourceUrl=>state.body.includes(sourceUrl)||state.html.includes(sourceUrl))});await page.close();}const matches=inspected.filter(row=>row.exactTitle||row.sourceKey||row.sourceUrl);await board.screenshot({path:'cafe/provider/precommit_duplicate_check.png',fullPage:true});const screenshotPath=await fs.resolvePath('cafe/provider/precommit_duplicate_check.png');await board.close();emit({status:boardState.body.includes(payload.board)?'ok':'board_mismatch',matches,duplicateCount:matches.length,checkedBy:['exact_title','source_key','short_source_url','long_source_url'],searches:searches.map(x=>({url:x.url,rows:x.rows.length})),screenshotPath});}
'''
    with LOCK.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        enforce_cafe_publish_window(source_key=source_key)
        precheck = run_repl(precheck_code, cwd=base, timeout=220, account="u0")
        if precheck.get("status") != "ok" or precheck.get("matches"):
            raise RuntimeError({"cafe_precommit_blocked": precheck})
        result = post_to_naver_cafe(
            manifest["title"], body, images,
            cafe_url="https://cafe.naver.com/f-e/cafes/26321967/menus/163?viewType=L",
            cta_text=manifest["tail"]["cta_text"], cta_link_url=manifest["tail"]["family_day_url"],
            source_label=manifest["tail"]["source_label"], source_url=manifest["tail"]["source_url"],
            source_long_url=manifest["tail"]["source_long_url"],
            board_name=manifest["category"], bold_enabled=True, highlight_enabled=False,
            publish=True, save_draft=False, account="u0",
        )
        if result.get("status") != "published" or "cafe.naver.com" not in result.get("url", ""):
            (evidence / "provider_uncertain_do_not_retry.json").write_text(
                json.dumps({"status": "uncertain_do_not_retry", "result": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            raise RuntimeError({"cafe_publish_uncertain_do_not_retry": result})
        verify_payload = {
            "url": result["url"], "title": manifest["title"], "cta": manifest["tail"]["cta_text"],
            "ctaUrl": manifest["tail"]["family_day_url"], "sourceUrl": manifest["tail"]["source_url"],
            "sourceLongUrl": manifest["tail"]["source_long_url"],
            "category": manifest["category"],
        }
        verify_code = JS_COMMON + "\nconst payload=" + _payload_expression(verify_payload) + ";\n" + r'''
const p=await openTab(`${payload.url}${payload.url.includes('?')?'&':'?'}provider_verify=${Date.now()}`);await sleep(4500);let text='',html='',images=0,oglinks=0,embeds=0,contexts=0;for(const ctx of await contextsFor(p)){try{const part=await ctx.evaluate(()=>({text:document.body?.innerText||'',html:document.body?.innerHTML||'',images:document.querySelectorAll('.se-image img,.se-module-image img').length,oglinks:document.querySelectorAll('.se-oglink').length,embeds:document.querySelectorAll('.se-oembed,.se-video').length}));text+='\n'+part.text;html+='\n'+part.html;images+=part.images;oglinks+=part.oglinks;embeds+=part.embeds;contexts++;}catch(_){}}const state={url:p.url(),contexts,titleExact:text.includes(payload.title),categoryExact:text.includes(payload.category),ctaExact:text.includes(payload.cta),ctaRaw:text.includes(payload.ctaUrl),sourceRaw:text.includes(payload.sourceUrl),sourceLongRaw:text.includes(payload.sourceLongUrl),images,oglinks,embeds};await p.screenshot({path:'cafe/provider/public_verification.png',fullPage:true});const screenshotPath=await fs.resolvePath('cafe/provider/public_verification.png');await p.close();emit({status:state.titleExact&&state.categoryExact&&state.ctaExact&&state.ctaRaw&&state.sourceRaw&&state.sourceLongRaw&&state.images===5&&state.oglinks>=1&&state.embeds>=1?'verified':'observed',...state,screenshotPath});
'''
        verified = run_repl(verify_code, cwd=base, timeout=150, account="u0")
        if verified.get("status") != "verified":
            (evidence / "published_but_verification_failed_do_not_retry.json").write_text(
                json.dumps({"status": "published_but_verification_failed_do_not_retry", "publish": result, "verify": verified}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            raise RuntimeError({"cafe_publication_exists_but_verification_failed_do_not_retry": verified})

        canonical_url, article_id = canonical_cafe_article_url(
            verified.get("url") or result.get("url") or ""
        )
        (evidence / "12_provider_success_reservation.json").write_text(
            json.dumps({
                "status": "provider_success_reserved",
                "sourceKey": source_key,
                "providerUrl": canonical_url,
                "articleId": article_id,
                "verifiedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    evidence_payload = {
        "status": "published_verified", "sourceKey": source_key, "provider": "Naver Cafe", "account": "u0",
        "category": manifest["category"], "providerUrl": canonical_url, "providerState": "public",
        "articleId": article_id,
        "exactTitle": manifest["title"], "images": result.get("images"), "quotes": result.get("quotes"),
        "ogCards": result.get("oglinks"), "youtubeCards": result.get("embeds"),
        "ctaRaw": verified["ctaRaw"], "sourceRaw": verified["sourceRaw"], "sourceLongRaw": verified["sourceLongRaw"],
        "publicVerification": verified, "precommitDuplicateCheck": precheck,
        "verifiedAt": datetime.now().astimezone().isoformat(timespec="seconds"), "doNotRetry": True,
    }
    evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    crm = crm_emit(source_key, evidence_path)
    (evidence / "14_crm_evidence.json").write_text(json.dumps(crm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if crm["exitCode"] != 0:
        raise RuntimeError({"provider_success_crm_failed": crm})
    evidence_payload["crm"] = {"status": "success", "channel": "naver_cafe", "stage": "sent", "count": 1}
    evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "published_verified", "providerUrl": canonical_url, "crm": evidence_payload["crm"]}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    manifest_path, base, provider, evidence = resolve_manifest(args.manifest)
    if args.validate_only:
        result = validate_cafe_eligibility(manifest_path, provider, evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "pass" else 1)
    publish(manifest_path, base, provider, evidence)


if __name__ == "__main__":
    main()
