"""Read actual Community post identities before/after a publish attempt."""
from __future__ import annotations

import json

from aside_browser import JS_COMMON, run_repl, _payload_expression


def inspect_posts(text: str, image_count: int, source_key: str, expected_channel: str,
                  *, community_url: str = "", account: str | None = None) -> dict:
    payload = dict(text=text.strip(), count=image_count, source=source_key,
                   expected=expected_channel, url=community_url)
    code = JS_COMMON + f"\nconst payload={_payload_expression(payload)};\n" + r'''
let p=await openTab(payload.url||'https://studio.youtube.com/');
try {
  let channel='', currentUrl='';
  for(let i=0;i<12;i++){
    await sleep(750);currentUrl=await p.evaluate(()=>location.href);
    channel=(currentUrl.match(/\/channel\/([^/?]+)/)||[])[1]||'';
    if(channel||/\/posts|\/community/.test(currentUrl))break;
  }
  if(!/\/posts|\/community/.test(currentUrl)){
    if(!channel)throw new Error('Community channel identity unavailable');
    p=await openTab(`https://www.youtube.com/channel/${channel}/posts`);
  }
  const rows=new Map();let exhausted=false, stable=0, last=-1;
  for(let page=0;page<20;page++){
    await sleep(1500);
    const state=await p.evaluate(()=>{
      const txt=x=>x?.simpleText||(x?.runs||[]).map(r=>r.text||'').join('');
      const cards=x=>{if(!x||typeof x!=='object')return 0;
        if(x.backstageImageRenderer)return 1;
        return Object.values(x).reduce((n,v)=>n+cards(v),0);};
      return {rows:[...document.querySelectorAll('ytd-backstage-post-renderer')].map(e=>{
        const d=e.data||{};return {id:d.postId,author:txt(d.authorText),
          channel:d.authorEndpoint?.browseEndpoint?.browseId||'',
          text:txt(d.contentText),images:cards(d.backstageAttachment)};
      }),continuation:!!document.querySelector('ytd-continuation-item-renderer')};
    });
    for(const row of state.rows)if(row.id)rows.set(row.id,row);
    if(rows.size===last)stable++;else stable=0;
    if(!state.continuation&&stable>=2){exhausted=true;break;}
    last=rows.size;
    await p.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
  }
  const all=[...rows.values()];
  if(!all.length)throw new Error('No provider post rows; absence is unverified');
  if(all.some(r=>r.author!==payload.expected||!r.channel||(channel&&r.channel!==channel)))
    throw new Error('Community author/channel mismatch');
  const related=all.filter(r=>r.text.trim()===payload.text||r.text.includes(payload.source));
  const exact=related.filter(r=>r.text.trim()===payload.text&&r.images===payload.count);
  let verified=null;
  if(related.length===1&&exact.length===1){
    const found=exact[0];
    p=await openTab(`https://www.youtube.com/post/${found.id}`);await sleep(4000);
    const direct=await p.evaluate(id=>{
      const d=[...document.querySelectorAll('ytd-backstage-post-renderer')]
        .map(e=>e.data||{}).find(d=>d.postId===id);
      if(!d)return null;
      const txt=x=>x?.simpleText||(x?.runs||[]).map(r=>r.text||'').join('');
      const cards=x=>!x||typeof x!=='object'?0:x.backstageImageRenderer?1:
        Object.values(x).reduce((n,v)=>n+cards(v),0);
      return {id:d.postId,author:txt(d.authorText),text:txt(d.contentText),
        channel:d.authorEndpoint?.browseEndpoint?.browseId||'',images:cards(d.backstageAttachment)};
    },found.id);
    if(!direct||JSON.stringify(direct)!==JSON.stringify({id:found.id,author:found.author,
      text:found.text,channel:found.channel,images:found.images}))
      throw new Error('Community direct post requery mismatch');
    verified={url:`https://www.youtube.com/post/${found.id}`,post_id:found.id,
      channel:found.author,channel_id:found.channel,images:found.images,
      captured_at:new Date().toISOString(),verified:true};
  }
  emit({status:verified?'verified':'not_verified',provider:verified,
    related_count:related.length,exact_count:exact.length,scanned:rows.size,exhausted});
} finally {await p.close();}
'''
    return run_repl(code, timeout=120, account=account)
