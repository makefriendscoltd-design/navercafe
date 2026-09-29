// Loaded by the official ego-browser runtime; never launches another browser.
const fs = await import('node:fs/promises');
const payload = __CAFE_EGO_PAYLOAD__;
const {publishedLocationReady} = await import(payload.helpersUrl);
const task = await taskSpace(payload.space.spaceId);
const page = task.page(payload.stage === 'precheck' ? payload.space.checkPage : payload.space.page);
const input = payload.input;
const norm = s => (s || '').replace(/[\s\u200b-\u200d\ufeff]/g, '');
const emit = x => console.log('CAFE_EGO_RESULT ' + JSON.stringify(x));
async function closeMaterial() {
  const visible = await page.evaluate(() => { const e=document.querySelector('button[aria-label="닫기"]'); return !!e && e.getBoundingClientRect().width>0; });
  if (visible) await page.click('button[aria-label="닫기"]');
}
async function tail() {
 await closeMaterial();
 const current=await page.evaluate(()=>{const e=[...document.querySelectorAll('.se-components-wrap > .se-component')].at(-1);return e?.classList.contains('se-text')?{id:e.querySelector('.se-text-paragraph')?.id,text:[...e.querySelectorAll('.__se-node')].map(n=>n.textContent).join('')}:null;});
 if(current){if(norm(current.text))throw Error('Expected empty trailing text; do not reposition within body');await page.click('#'+current.id);}
 else await page.click('.se-canvas-bottom-button');
}
async function state() {
 return page.evaluate(()=>({url:location.href,title:document.querySelector('textarea')?.value,category:document.querySelector('.FormSelectButton button')?.textContent.trim(),components:[...document.querySelectorAll('.se-components-wrap > .se-component')].map(e=>({id:e.id,type:['quote','image','text','og','embed'].find((_,i)=>e.classList.contains(['se-quotation','se-image','se-text','se-oglink','se-oembed'][i])),text:e.innerText,quote:e.querySelector('.se-quote')?.textContent,images:[...e.querySelectorAll('img')].map(x=>({loaded:x.complete&&x.naturalWidth>0,src:x.src}))})),paragraphs:[...document.querySelectorAll('.se-component.se-text .se-text-paragraph')].map(e=>e.textContent)}));
}
function verify(s) {
 const meaningful=s.components.filter(c=>c.type!=='text'||norm(c.text));
 const prefix=meaningful.slice(0,input.sequence.length);
 const checks={title:s.title===input.title,category:s.category===input.category,sequence:JSON.stringify(prefix.map(c=>c.type))===JSON.stringify(input.sequence),quotes:JSON.stringify(s.components.filter(c=>c.type==='quote').map(c=>norm(c.quote)))===JSON.stringify(input.quotes.map(norm)),body:input.texts.every(t=>norm(s.paragraphs.join('')).includes(norm(t))),images:s.components.filter(c=>c.type==='image').length===5,imagesLoaded:s.components.filter(c=>c.type==='image').every(c=>c.images.some(x=>x.loaded&&x.src.startsWith('https://cafeptthumb-phinf.pstatic.net/'))),cta:s.paragraphs.some(t=>norm(t)===norm(input.tail.cta_text)),familyRaw:s.paragraphs.includes(input.tail.family_day_url),sourceLabel:s.paragraphs.includes(input.tail.source_label),sourceRaw:s.paragraphs.includes(input.tail.source_url),sourceLongRaw:s.paragraphs.includes(input.tail.source_long_url),og:s.components.filter(c=>c.type==='og').length===1,embed:s.components.filter(c=>c.type==='embed').length===1&&s.components.filter(c=>c.type==='embed')[0].images.some(x=>x.src.includes('/'+input.source_key+'/')),tailOrder:JSON.stringify(meaningful.slice(input.sequence.length-(input.sequence.at(-1)==='text'?1:0)).map(c=>c.type))===JSON.stringify(['text','og','text','embed'])};
 return {status:Object.values(checks).every(Boolean)?'pass':'fail',checks,state:s};
}
if(payload.stage==='precheck') {
 const searches=[]; const candidates=new Map();
 for(const query of [input.title,input.source_key]) {
  await page.goto('https://cafe.naver.com/f-e/cafes/26321967/menus/0?q='+encodeURIComponent(query)+'&t='+Date.now());
  await page.waitForFunction(q=>[...document.querySelectorAll('input[placeholder="검색어를 입력해주세요"]')].some(e=>e.value===q)&&(document.body.innerText.includes('등록된 게시글이 없습니다.')||document.querySelector('table a[href*="/articles/"]')),query,{timeout:25000});
  const result=await page.evaluate(()=>({text:document.body.innerText,rows:[...document.querySelectorAll('table a[href*="/articles/"]')].map(a=>({url:a.href,title:a.innerText}))}));
  const paginated=await page.evaluate(()=>[...document.querySelectorAll('button,a')].some(e=>e.getBoundingClientRect().width>0&&(/^2$/.test(e.textContent.trim())||e.getAttribute('aria-label')==='다음 페이지')));
  if(result.rows.length>60||paginated)throw Error('duplicate_search_requires_pagination_review');
  for(const row of result.rows) {const m=row.url.match(/\/articles\/(\d+)/);if(m)candidates.set(m[1],row);}
  searches.push({query,rows:result.rows.length,empty:result.text.includes('등록된 게시글이 없습니다.')});
 }
 const matches=[];
 for(const [id,row] of candidates) {
  await page.goto('https://cafe.naver.com/ca-fe/cafes/26321967/articles/'+id);
  await page.waitForFunction(()=>!!document.querySelector('.article_viewer,.ArticleContentBox'),undefined,{timeout:25000});
  const data=await page.evaluate(()=>({text:document.querySelector('.article_viewer,.ArticleContentBox').innerText,title:document.querySelector('.title_text')?.textContent||''}));
  if(norm(data.title)===norm(input.title)||data.text.includes(input.source_key))matches.push({id,url:'https://cafe.naver.com/westudyssat/'+id});
 }
 emit({status:matches.length?'duplicate':'ok',matches,searches,duplicateCount:matches.length,checkedAt:new Date().toISOString()});
} else if(payload.stage==='fill') {
 await page.goto('https://cafe.naver.com/ca-fe/cafes/26321967/menus/163/articles/write?boardType=L');
 await page.waitForSelector('textarea[placeholder="제목을 입력해 주세요."]',{timeout:20000});
 await page.waitForFunction(()=>document.readyState==='complete'&&document.querySelector('.se-components-wrap > .se-component .se-text-paragraph')&&document.querySelector('[contenteditable="true"]'),undefined,{timeout:20000});
 if(await page.evaluate(()=>!!document.querySelector('textarea')?.value||[...document.querySelectorAll('.se-component .__se-node')].some(e=>e.textContent.trim())))throw Error('editor_not_empty');
 await page.fill('textarea[placeholder="제목을 입력해 주세요."]',input.title);
 await page.waitForFunction(title=>document.querySelector('textarea')?.value===title,input.title,{timeout:10000});
 let images=0,quotes=0,lastKind=null;
 for(const token of input.tokens) {
  if(token.kind!=='image'){
   if(lastKind==='text'){await page.keyboard.press('Enter');await page.keyboard.press('Enter');}
   else await tail();
  } else if(lastKind!=='text') await tail();
  if(token.kind==='quote') {
   await page.click('.se-insert-quotation-default-toolbar-button');await page.keyboard.paste(token.text);quotes++;
   await page.waitForFunction(({n,text})=>{const a=[...document.querySelectorAll('.se-quotation .se-quote')];return a.length===n&&a.at(-1).textContent.replace(/\s/g,'')===text.replace(/\s/g,'');},{n:quotes,text:token.text},{timeout:10000});
  } else if(token.kind==='text') {
   await page.keyboard.paste(token.text);
   await page.waitForFunction(text=>document.querySelector('.se-components-wrap').textContent.replace(/\s/g,'').includes(text.replace(/\s/g,'')),token.text,{timeout:10000});
  } else {
   const chooserPromise=page.waitForFileChooser({timeout:10000});await page.click('.se-image-toolbar-button');const chooser=await chooserPromise;await chooser.setFiles(input.images[images]);images++;
   await page.waitForFunction(n=>document.querySelectorAll('.se-component.se-image').length===n,images,{timeout:30000});
  }
  lastKind=token.kind;
 }
 if(lastKind==='text'){await page.keyboard.press('Enter');await page.keyboard.press('Enter');}else await tail();
 await page.keyboard.paste(input.tail.cta_text);
 await page.waitForFunction(text=>document.querySelector('.se-components-wrap').textContent.includes(text),input.tail.cta_text,{timeout:10000});
 await page.keyboard.press('Enter');await page.keyboard.press('Enter');
 await page.keyboard.paste(input.tail.family_day_url);await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelectorAll('.se-oglink').length===1,undefined,{timeout:30000});
 await tail();await page.keyboard.paste(input.tail.source_label+'\n'+input.tail.source_long_url);
 await page.waitForFunction(text=>document.querySelector('.se-components-wrap').textContent.includes(text),input.tail.source_long_url,{timeout:10000});
 await page.keyboard.press('Enter');
 await page.keyboard.paste(input.tail.source_url);await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelectorAll('.se-oembed').length>=1,undefined,{timeout:30000});
 // Pasting both canonical source URLs may generate two identical provider cards.
 let embeds=await page.evaluate(()=>[...document.querySelectorAll('.se-component.se-oembed')].map(e=>({id:e.id,src:e.querySelector('img')?.src})));
 if(embeds.length>2||embeds.some(e=>!e.src?.includes('/'+input.source_key+'/')))throw Error('unexpected_video_embed');
 if(embeds.length===2){await page.click('#'+embeds[1].id+' .se-module-oembed');await page.click('.se-delete-toolbar-button');await page.waitForFunction(()=>document.querySelectorAll('.se-oembed').length===1,undefined,{timeout:10000});}
 // Material search and lazy placeholders are real SmartEditor states, not uploaded images.
 const ids=await page.evaluate(()=>[...document.querySelectorAll('.se-component.se-image')].map(e=>e.id));
 for(const id of ids){await page.click('#'+id+' .se-module-image');await page.waitForFunction(id=>{const e=document.querySelector('#'+id+' img');return e?.src.startsWith('https://cafeptthumb-phinf.pstatic.net/')&&e.complete&&e.naturalWidth>0;},id,{timeout:20000});}
 const result=verify(await state());await page.screenshot({path:payload.screenshot,fullPage:true});emit(result);
} else if(payload.stage==='verify_editor') {emit(verify(await state()));
} else if(payload.stage==='publish') {
 const before=verify(await state());if(before.status!=='pass')throw Error('editor_changed_before_publish');
 // Python persists an uncertainty barrier before starting this stage.
 const selector=await page.evaluate(()=>{const e=[...document.querySelectorAll('a.BaseButton--skinGreen,button.btn_register')].filter(e=>e.textContent.trim()==='등록');if(e.length!==1)throw Error('register_not_unique');return e[0].tagName==='A'?'a.BaseButton--skinGreen':'button.btn_register';});
 await page.click(selector);
 await page.waitForFunction(publishedLocationReady,undefined,{timeout:30000});
 emit({status:'published',url:await page.url()});
} else if(payload.stage==='verify_public') {
 await page.goto(payload.url);await page.waitForFunction(()=>!!document.querySelector('.article_viewer,.ArticleContentBox'),undefined,{timeout:25000});
 const result=await page.evaluate(()=>{const root=document.querySelector('.article_viewer,.ArticleContentBox');return {url:location.href,title:document.querySelector('.title_text')?.textContent?.trim(),text:root.innerText,html:root.innerHTML,images:root.querySelectorAll('.se-image').length,quotes:[...root.querySelectorAll('.se-quotation .se-quote')].map(e=>e.textContent),oglinks:root.querySelectorAll('.se-oglink').length,embeds:root.querySelectorAll('.se-oembed,.se-video').length,category:document.querySelector('.link_board')?.textContent?.trim()};});
 const checks={title:result.title===input.title,category:result.category===input.category,images:result.images===5,quotes:JSON.stringify(result.quotes.map(norm))===JSON.stringify(input.quotes.map(norm)),body:input.texts.every(t=>norm(result.text).includes(norm(t))),cta:result.text.includes(input.tail.cta_text),ctaRaw:result.text.includes(input.tail.family_day_url),sourceRaw:result.text.includes(input.tail.source_url),sourceLongRaw:result.text.includes(input.tail.source_long_url),og:result.oglinks>=1,embed:result.embeds>=1&&result.html.includes(input.source_key)};
 await page.screenshot({path:payload.screenshot,fullPage:true});emit({status:Object.values(checks).every(Boolean)?'verified':'observed',checks,...result});
} else throw Error('Unknown stage');
