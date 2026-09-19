"""이미 올라간 나민수 AI 쇼츠의 설명글을 채널 정책 정본대로 바꾼다.

정책 정본은 "본문에 채널 목적과 cta_block 포함"을 요구한다. 이 요구가 발행 경로에
들어가기 전에 올라간 쇼츠는 대본과 원본 링크만 설명글에 있다. 새 발행과 같은
`shorts_description()`으로 설명글을 다시 만들어 Studio에 넣는다.

설명글 한 칸만 바꾼다. 입력은 업로드 도구와 같은 방식(전체 선택 후 지우고 한 글자씩
입력)이고, 비교도 같은 공백 정규화를 쓴다. 저장 뒤 편집 화면을 새로 열어 설명글이
정본 문구와 같을 때만 성공으로 친다. 올라간 쇼츠의 발행 journal과 매니페스트는
건드리지 않고, 바꾼 내역은 영상 폴더의 증거 파일에 따로 남긴다.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from datetime import datetime
from pathlib import Path

from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import ASIDE_ACCOUNT, load_channel_policy, shorts_description
from shorts_related_video import VIDEO_ID_RE, pin_channel

PROJECT = Path(__file__).resolve().parent
PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")

COMMON = r'''
const compact=s=>(s||'').replace(/[​-‍⁠﻿]/g,'').replace(/ /g,' ').replace(/\r\n?/g,'\n').replace(/\s+/g,'');
const openEdit=async tag=>{
  const p=await openTab(`https://studio.youtube.com/video/${payload.videoId}/edit?${tag}=${Date.now()}`);
  for(let i=0;i<60;i++){
    const err=/오류가 발생했습니다|권한이 없습니다/.test(await p.evaluate(()=>document.body?.innerText||''));
    if(err)return {p,error:'edit-page-error'};
    if((await p.locator('#title-textarea #textbox').count())===1&&(await p.locator('#description-textarea #textbox').count())===1)return {p};
    await sleep(500);
  }
  return {p,error:'edit-page-not-ready'};
};
'''

READ_JS = COMMON + r'''
const {p,error}=await openEdit('desc_check');
if(error){emit({status:'blocked',error});}
else{
  const text=((await p.locator('#description-textarea #textbox').innerText())||'').trim();
  emit({status:'ok',matches:compact(text)===compact(payload.description),length:text.length});
}
'''

SET_JS = COMMON + r'''
const {p,error}=await openEdit('desc_set');
if(error){emit({status:'blocked',error});}
else{
  const box=p.locator('#description-textarea #textbox');
  await box.click(); await p.keyboard.press('Meta+A'); await p.keyboard.press('Backspace');
  await box.pressSequentially(payload.description,{delay:1});
  await sleep(800);
  const typed=((await box.innerText())||'').trim();
  if(compact(typed)!==compact(payload.description)){
    emit({status:'blocked',error:'description-fill-mismatch',typedLength:typed.length});
  }else{
    const save=p.locator('ytcp-button#save');
    if((await save.count())!==1){emit({status:'blocked',error:'save-button-cardinality'});}
    else{
      await save.click();
      let saved=false;
      for(let i=0;i<40;i++){await sleep(500);if((await save.getAttribute('aria-disabled'))==='true'){saved=true;break;}}
      emit({status:saved?'saved':'save-unconfirmed'});
    }
  }
}
'''


def _run(js: str, payload: dict) -> dict:
    code = JS_COMMON + "\nconst payload=" + _payload_expression(payload) + ";\n" + js
    return run_repl(code, timeout=110, account=ASIDE_ACCOUNT)


def desired_description(source_root: Path, policy: dict) -> str:
    source_key = source_root.name.rsplit("-", 1)[0]
    script = (source_root / "shorts" / "07_script_final.txt").read_text(encoding="utf-8").strip()
    urls = [f"https://youtu.be/{source_key}", f"https://www.youtube.com/watch?v={source_key}"]
    return shorts_description(script, urls, policy)


def update_description(video_id: str, source_root: Path) -> dict:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"영상 id 형식이 아닙니다: {video_id}")
    policy = load_channel_policy()
    description = desired_description(source_root, policy)
    if len(description) > 5000:
        raise RuntimeError(f"설명글이 5000자를 넘습니다: {len(description)}")
    payload = {"videoId": video_id, "description": description}
    PROVIDER_LOCK.touch(exist_ok=True)
    with PROVIDER_LOCK.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before = _run(READ_JS, payload)
        if before.get("status") != "ok":
            return {"video_id": video_id, "status": "blocked", "stage": "read", **before}
        if before.get("matches"):
            return {"video_id": video_id, "status": "already_set"}
        result = _run(SET_JS, payload)
        if result.get("status") != "saved":
            return {"video_id": video_id, "status": "blocked", "stage": "set", **result}
        after = _run(READ_JS, payload)
    verified = after.get("status") == "ok" and after.get("matches") is True
    outcome = {"video_id": video_id, "status": "verified" if verified else "unverified",
               "source_root": source_root.name, "description_length": len(description),
               "cta_included": policy["cta_block"] in description}
    evidence = source_root / "shorts" / "provider" / "description_policy_update.json"
    evidence.write_text(json.dumps({**outcome, "updated_at": datetime.now().astimezone().isoformat(),
                                    "description": description}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", nargs="+", help="VIDEO_ID=출력폴더이름 (예: Ym1sWzWOYI4=nSfEL1Y-nUk-20260915)")
    parser.add_argument("--skip-channel-pin", action="store_true")
    args = parser.parse_args(argv)
    if not args.skip_channel_pin:
        pin_channel()
    results = []
    for pair in args.pairs:
        vid, _, folder = pair.partition("=")
        try:
            r = update_description(vid, PROJECT / "outputs" / folder)
        except Exception as exc:  # noqa: BLE001 - 한 건 실패가 나머지를 막지 않게 기록만 한다
            r = {"video_id": vid, "status": "error", "error": str(exc)[:300]}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    good = sum(r["status"] in ("verified", "already_set") for r in results)
    print(f"설명글 확인 {good}/{len(results)}", flush=True)
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
