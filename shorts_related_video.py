"""나민수 AI 쇼츠의 관련 동영상에 채널 정책 정본의 소개 영상을 건다.

정책 정본(2026-09-14 확정)은 "앞으로 업로드하는 쇼츠는 introduction_video.video_id를
관련 동영상으로 지정하고 저장 후 재조회로 확인한다"고 정한다. Studio 관련 동영상
선택 창은 후보 카드에 영상 id를 드러내지 않으므로, 정본의 소개 영상 제목으로 "내
동영상"을 검색해 제목이 정확히 같은 카드가 딱 하나일 때만 고른다. 소개 영상 id와
제목의 대응은 정본이 보증하고, 채널 안에서 그 제목이 유일하다는 것은 검색 결과가
보증한다. 둘 중 하나라도 어긋나면 아무것도 저장하지 않는다.

이 도구는 관련 동영상 한 칸만 바꾼다. 저장한 뒤 편집 화면을 새로 열어 그 칸이 소개
영상 제목으로 보일 때만 성공으로 본다.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path

from aside_browser import JS_COMMON, _payload_expression, run_repl
from content_production_policy import ASIDE_ACCOUNT, load_channel_policy

PROVIDER_LOCK = Path("/tmp/aimax-aside-u0-provider.lock")
SELECT_CHANNEL = Path(
    "/Users/apple/orca/workspaces/navercafe/숏폼/outputs/shorts-seven-20260914/select_provider_channel.py"
)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 편집 화면을 열고 관련 동영상 칸의 현재 값을 읽는다. 선택·저장은 하지 않는다.
READ_JS = r'''
const p=await openTab(`https://studio.youtube.com/video/${payload.videoId}/edit?related_check=${Date.now()}`);
let state={};
const end=Date.now()+30000;
while(Date.now()<end){
  state=await p.evaluate(()=>{
    const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
    const trig=[...document.querySelectorAll('ytcp-dropdown-trigger')].filter(vis)
      .find(t=>/관련 동영상/.test(t.innerText||''));
    const lines=trig?(trig.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean):[];
    return {titleBox:document.querySelectorAll('#title-textarea #textbox').length,
            hasTrigger:!!trig, related:lines.filter(x=>x!=='관련 동영상').join(' / '),
            error:/오류가 발생했습니다|권한이 없습니다/.test(document.body?.innerText||'')};
  });
  if(state.error||(state.titleBox===1&&state.hasTrigger))break;
  await sleep(500);
}
emit({status:state.error?'blocked':'ok', ...state});
'''

# 소개 영상을 골라 저장한다. 제목이 정확히 같은 카드가 딱 하나가 아니면 저장하지 않는다.
SET_JS = r'''
const p=await openTab(`https://studio.youtube.com/video/${payload.videoId}/edit?related_set=${Date.now()}`);
const vis=async loc=>(await loc.count())>0&&await loc.first().isVisible();
let ok=false;
for(let i=0;i<60;i++){
  if((await p.locator('#title-textarea #textbox').count())===1&&
     await vis(p.locator('ytcp-dropdown-trigger').filter({hasText:'관련 동영상'}))){ok=true;break;}
  await sleep(500);
}
if(!ok){emit({status:'blocked',error:'edit-page-not-ready'});}
else{
  await p.locator('ytcp-dropdown-trigger').filter({hasText:'관련 동영상'}).first().click();
  // 선택 창이 뜨는 데 걸리는 시간이 들쭉날쭉해서 고정 대기로는 가끔 검색칸을 놓친다.
  const search=p.locator('input[placeholder="내 동영상 검색"]').first();
  for(let i=0;i<30&&!(await search.count()&&await search.isVisible());i++) await sleep(500);
  if(!(await search.count())){emit({status:'blocked',error:'related-search-missing'});}
  else{
    await search.fill(payload.introTitle);
    await sleep(4000);
    const pick=await p.evaluate((title)=>{
      const dlg=[...document.querySelectorAll('tp-yt-paper-dialog')].filter(x=>x.getBoundingClientRect().width>0).pop();
      if(!dlg)return {count:-1};
      const cards=[...dlg.querySelectorAll('ytcp-entity-card')]
        .filter(c=>c.getBoundingClientRect().width>0&&(c.innerText||'').trim().split('\n').map(x=>x.trim()).includes(title));
      if(cards.length===1){cards[0].click();}
      return {count:cards.length};
    }, payload.introTitle);
    if(pick.count!==1){
      await p.keyboard.press('Escape');
      emit({status:'blocked',error:`intro-card-cardinality:${pick.count}`});
    }else{
      await sleep(2500);
      const shown=await p.evaluate(()=>{
        const t=[...document.querySelectorAll('ytcp-dropdown-trigger')].find(x=>/관련 동영상/.test(x.innerText||''));
        return t?(t.innerText||'').trim():'';
      });
      if(!shown.includes(payload.introTitle)){
        emit({status:'blocked',error:'selection-not-reflected',shown});
      }else{
        const save=p.locator('ytcp-button#save');
        if((await save.count())!==1){emit({status:'blocked',error:'save-button-cardinality'});}
        else{
          await save.click();
          let saved=false;
          for(let i=0;i<40;i++){
            await sleep(500);
            const dis=await save.getAttribute('aria-disabled');
            if(dis==='true'){saved=true;break;}
          }
          emit({status:saved?'saved':'save-unconfirmed',shown});
        }
      }
    }
  }
}
'''


def _run(js: str, payload: dict) -> dict:
    code = JS_COMMON + "\nconst payload=" + _payload_expression(payload) + ";\n" + js
    return run_repl(code, timeout=110, account=ASIDE_ACCOUNT)



def live_intro_title(video_id: str) -> str | None:
    """소개 영상의 현재 제목을 공급자에서 직접 읽는다.

    이 영상은 제목 A/B 테스트 대상이라 정본에 적힌 제목이 실제와 어긋날 수 있다.
    선택 창이 영상 id를 드러내지 않으므로 제목으로 찾을 수밖에 없고, 정본 제목으로
    못 찾을 때 실제 제목으로 한 번 더 찾기 위해 쓴다.
    """
    try:
        import yt_dlp

        from youtube_source_options import source_options

        options = {**source_options(), "skip_download": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://youtu.be/{video_id}", download=False)
        title = str(info.get("title") or "").strip()
        return title or None
    except Exception:
        return None


def pin_channel() -> None:
    """Studio 활성 채널이 다른 채널로 가 있으면 편집 화면이 권한 오류로 막힌다."""
    completed = subprocess.run([sys.executable, str(SELECT_CHANNEL)], capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise RuntimeError("나민수 AI 채널 고정 실패: " + (completed.stderr or completed.stdout).strip()[-200:])


def set_related_video(video_id: str) -> dict:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"영상 id 형식이 아닙니다: {video_id}")
    policy = load_channel_policy()
    intro_title = policy["introduction_video_title"].strip()
    if not intro_title:
        raise RuntimeError("채널 정책 정본에 소개 영상 제목이 없어 관련 동영상을 안전하게 고를 수 없습니다.")
    titles = [intro_title]
    live = live_intro_title(policy["introduction_video_id"])
    if live and live != intro_title:
        titles.append(live)
    payload = {"videoId": video_id, "introTitle": intro_title}
    PROVIDER_LOCK.touch(exist_ok=True)
    with PROVIDER_LOCK.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before = _run(READ_JS, payload)
        if before.get("status") != "ok":
            return {"video_id": video_id, "status": "blocked", "stage": "read", **before}
        if any(t in (before.get("related") or "") for t in titles):
            return {"video_id": video_id, "status": "already_set", "related": before.get("related")}
        # 정본 제목으로 못 찾으면 공급자의 현재 제목으로 한 번 더 찾는다(A/B 테스트 대비).
        for attempt, title in enumerate(titles):
            payload = {"videoId": video_id, "introTitle": title}
            result = _run(SET_JS, payload)
            if result.get("status") == "saved":
                intro_title = title
                break
            if not str(result.get("error") or "").startswith("intro-card-cardinality:0"):
                break
        if result.get("status") != "saved":
            return {"video_id": video_id, "status": "blocked", "stage": "set",
                    "titles_tried": titles, **result}
        # 정본 요구: 저장 후 재조회로 확인한다.
        after = _run(READ_JS, payload)
    verified = after.get("status") == "ok" and intro_title in (after.get("related") or "")
    return {"video_id": video_id, "status": "verified" if verified else "unverified",
            "before": before.get("related"), "after": after.get("related"),
            "matched_title": intro_title,
            "introduction_video_id": policy["introduction_video_id"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_ids", nargs="+")
    parser.add_argument("--skip-channel-pin", action="store_true")
    args = parser.parse_args(argv)
    if not args.skip_channel_pin:
        pin_channel()
    results = []
    for vid in args.video_ids:
        try:
            r = set_related_video(vid)
        except Exception as exc:  # noqa: BLE001 - 한 건 실패가 나머지를 막지 않게 기록만 한다
            r = {"video_id": vid, "status": "error", "error": str(exc)[:300]}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
    good = sum(r["status"] in ("verified", "already_set") for r in results)
    print(f"관련 동영상 확인 {good}/{len(results)}", flush=True)
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
