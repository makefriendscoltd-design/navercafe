"""Write a scheduled short's real title and confirm it in the list, not just the form.

An upload that was interrupted keeps the draft sentinel as its title. The save
step verified the title by reading the same edit form it had just typed into, so
a value that never reached the provider still read back as correct and seven
shorts went out titled ``shorts-<source>-<hash>``. Confirming against the video
list -- the same place a person looks -- is what catches that.

This edits metadata only. It never touches the video, its description or its
schedule.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from aside_browser import JS_COMMON, _payload_expression, run_repl
from youtube_shorts_aside_adapter import CHANNEL_ID, STUDIO_SHORTS_URL

ACCOUNT = "u0"

SET_TITLE_JS = r"""
let p=null,saveClicks=0,toastSeen=false;
const openEdit=async()=>{
  p=await openTab(`https://studio.youtube.com/video/${payload.id}/edit?title_repair=${Date.now()}`);
  const end=Date.now()+40000;
  while(Date.now()<end){
    if(p.url().includes(`/video/${payload.id}/edit`)
       && await p.locator('#title-textarea #textbox').count()===1
       && await p.locator('#description-textarea #textbox').count()===1) return;
    await sleep(300);
  }
  throw new Error('edit-page-not-ready');
};
const readTitle=async()=>((await p.locator('#title-textarea #textbox').innerText())||'').trim();
const readDesc=async()=>((await p.locator('#description-textarea #textbox').innerText())||'').trim();
try{
  await openEdit();
  const before=await readTitle(), descBefore=await readDesc();
  if(![payload.sentinel,payload.title].includes(before))throw new Error('unexpected-title:'+before);
  if(descBefore!==payload.description.trim()&&descBefore!==(payload.approvedDescription||'').trim())throw new Error('description-drifted');
  if(before!==payload.title){
    const loc=p.locator('#title-textarea #textbox');
    await loc.click();await p.keyboard.press('Meta+A');await p.keyboard.press('Backspace');
    await loc.pressSequentially(payload.title,{delay:1});
    await p.keyboard.press('Tab');
    if((await readTitle())!==payload.title)throw new Error('title-entry-mismatch');
    const save=p.locator('ytcp-button#save');
    if(await save.count()!==1||!await save.isEnabled())throw new Error('save-unavailable');
    saveClicks++;await save.click();
    const end=Date.now()+30000;while(Date.now()<end&&await save.isEnabled())await sleep(250);
    if(await save.isEnabled())throw new Error('save-unconfirmed');
    // The saved toast can come and go before it is sampled. It is a hint, not the
    // proof: the list check below is what decides whether the title really landed.
    const toast=p.getByText('변경사항이 저장됨',{exact:true}),te=Date.now()+20000;
    while(Date.now()<te&&!toastSeen){
      toastSeen=await toast.count()===1&&await toast.isVisible();
      if(!toastSeen)await sleep(200);
    }
  }
  await p.close();p=null;
  // The form can read back a value the provider never stored, so confirm against
  // the video list instead: the row model is what a person actually sees.
  p=await openTab(payload.list_url);await sleep(9000);
  let listed=null;
  for(let page=1;page<=80&&listed===null;page++){
    listed=await p.evaluate(id=>{
      const row=[...document.querySelectorAll('ytcp-video-row')].find(e=>e.video?.videoId===id);
      if(!row)return null;
      const node=row.querySelector('#video-title');
      return {modelTitle:(row.video?.title||'').trim(),
              rowTitle:(node?.getAttribute('title')||node?.innerText||'').trim(),
              descLen:(row.video?.description||'').length};
    },payload.id);
    if(listed!==null)break;
    const next=p.locator('#navigate-after,ytcp-icon-button#navigate-after');
    if(await next.count()!==1)break;
    const dis=(await next.getAttribute('aria-disabled'))==='true'||(await next.getAttribute('disabled'))!==null||!await next.isEnabled();
    if(dis)break;
    await next.click();await sleep(3500);
  }
  if(!listed)throw new Error('row-not-found-in-list');
  if(listed.modelTitle!==payload.title||listed.rowTitle!==payload.title)
    throw new Error('list-title-mismatch:'+listed.modelTitle);
  emit({status:'pass',provider_id:payload.id,save_clicks:saveClicks,saved_toast_seen:toastSeen,
        title_before:before,title:listed.modelTitle,list_description_length:listed.descLen,
        list_verified:true});
}catch(error){emit({status:'blocked',provider_id:payload.id,save_clicks:saveClicks,
                    error:String(error?.message||error)});}
finally{try{if(p)await p.close();}catch(_){}}
"""


def approved_description(root: Path, provider_id: str) -> str:
    """The description a verified policy pass already wrote onto this video."""
    update = root / "provider/description_policy_update.json"
    if not update.is_file():
        return ""
    try:
        value = json.loads(update.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if value.get("status") != "verified" or value.get("video_id") != provider_id:
        return ""
    return str(value.get("description") or "")


def repair(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    manifest = json.loads((root / "07_provider_manifest.json").read_text(encoding="utf-8"))
    journal = json.loads((root / "provider/journal.json").read_text(encoding="utf-8"))
    def attached_provider_id() -> str:
        found = ""
        for receipt in sorted((root / "provider").glob("attachment_receipt*.json")):
            try:
                value = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if value.get("status") == "attached" and value.get("provider_id"):
                found = str(value["provider_id"])
        return found

    provider_id = ((journal.get("replacement_retirement") or {}).get("new_provider_id")
                   or (journal.get("verified") or {}).get("provider_id")
                   or attached_provider_id())
    if not provider_id:
        raise RuntimeError("이 산출물에서 업로드된 영상 ID를 찾지 못했습니다.")
    payload = {
        "id": provider_id,
        "title": manifest["title"],
        "description": manifest["description"],
        "approvedDescription": approved_description(root, provider_id),
        "sentinel": manifest.get("draft_sentinel")
        or f"shorts-{manifest['source_key']}-{manifest['final_mp4_sha256'][:12]}",
        "list_url": STUDIO_SHORTS_URL,
        "channel_id": CHANNEL_ID,
    }
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + SET_TITLE_JS
    result = run_repl(code, timeout=300, account=ACCOUNT)
    if result.get("status") != "pass":
        raise RuntimeError(f"제목 복구 실패 {provider_id}: {result.get('error')}")
    record = {**result, "source_key": manifest["source_key"],
              "repaired_at": datetime.now(timezone.utc).isoformat()}
    (root / "provider/title-repair.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args = parser.parse_args(argv)
    print(json.dumps(repair(args.root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
