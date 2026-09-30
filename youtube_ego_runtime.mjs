// Loaded by the official ego-browser runtime; never launches another browser.
//
// The YouTube Studio provider programs were written against Aside's
// Playwright-style page API.  This file gives them the small subset they use
// (openTab, locator chains, getByText, keyboard, setInputFiles) on top of an
// ego-browser TaskSpace, so the same audited programs run unchanged.
//
// All in-page code is sent as expression strings: Studio enforces Trusted
// Types, so page-side eval/new Function would be blocked, while CDP evaluation
// of a complete expression is not.
const __egoConfig = __YT_EGO_CONFIG__;
const payload = __YT_EGO_PAYLOAD__;
const __path = await import('node:path');
const __task = await taskSpace(__egoConfig.spaceId);
const RESULT_MARKER = '__ASIDE_RESULT__';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const emit = value => console.log(RESULT_MARKER + JSON.stringify(value));
const __sourceOf = fn => (typeof fn === 'function' ? fn.toString() : String(fn));
const __json = value => (value === undefined ? 'undefined' : JSON.stringify(value));

// Page-side resolver: returns matching elements for a locator step chain.
const __RESOLVE = String.raw`(steps)=>{
  const deepAll=(root,sel)=>{const out=[];const walk=node=>{let found=[];try{found=node.querySelectorAll(sel);}catch(e){throw new Error('invalid selector: '+sel);}for(const el of found)out.push(el);for(const el of node.querySelectorAll('*'))if(el.shadowRoot)walk(el.shadowRoot);};walk(root);return out;};
  const everything=root=>{const out=[];const walk=node=>{for(const el of node.querySelectorAll('*')){out.push(el);if(el.shadowRoot)walk(el.shadowRoot);}};walk(root);return out;};
  const norm=s=>(s||'').replace(/[​-‍﻿]/g,'').replace(/\s+/g,' ').trim();
  const textMatch=(el,step)=>{if(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(el.tagName))return false;const t=norm(el.innerText!==undefined&&el.innerText!==null?el.innerText:el.textContent);if(!t)return false;if(step.regex){const re=new RegExp(step.regex.source,step.regex.flags);return re.test(t);}if(step.exact)return t===norm(step.text);return t.toLowerCase().includes(norm(step.text).toLowerCase());};
  let current=[document];
  for(const step of steps){
    if(step.kind==='nth'){const i=step.index<0?current.length+step.index:step.index;current=current[i]?[current[i]]:[];continue;}
    const next=[];const seen=new Set();
    for(const root of current){
      let found;
      if(step.kind==='css')found=deepAll(root,step.selector);
      else{const all=everything(root).filter(el=>textMatch(el,step));found=all.filter(el=>!all.some(o=>o!==el&&el.contains(o)));}
      for(const el of found)if(!seen.has(el)){seen.add(el);next.push(el);}
    }
    current=next;
  }
  return current.filter(el=>el!==document);
}`;

const __VISIBLE = String.raw`(el)=>{if(!el||!el.isConnected)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';}`;
const __ENABLED = String.raw`(el)=>{if(!el)return false;if(el.disabled===true)return false;for(let n=el;n;n=n.parentElement){if(n.getAttribute&&n.getAttribute('aria-disabled')==='true')return false;if(n.tagName==='FIELDSET'&&n.disabled)return false;}return true;}`;

class __Keyboard {
  constructor(owner) { this.owner = owner; }
  async press(key) { await this.owner.raw.keyboard.press(key); }
  async type(text) { await this.owner.raw.keyboard.insertText(String(text)); }
  async insertText(text) { await this.owner.raw.keyboard.insertText(String(text)); }
}

class __Locator {
  constructor(owner, steps) { this.owner = owner; this.steps = steps; }
  locator(selector) { return new __Locator(this.owner, [...this.steps, { kind: 'css', selector }]); }
  getByText(text, options = {}) { return new __Locator(this.owner, [...this.steps, __textStep(text, options)]); }
  first() { return this.nth(0); }
  last() { return this.nth(-1); }
  nth(index) { return new __Locator(this.owner, [...this.steps, { kind: 'nth', index }]); }
  async _eval(body, arg) {
    const expr = `(async()=>{const __els=(${__RESOLVE})(${JSON.stringify(this.steps)});const __vis=${__VISIBLE};const __en=${__ENABLED};const __arg=${__json(arg)};const __r=await (${body})(__els,__arg);return {r:__r,href:location.href};})()`;
    const out = await this.owner.raw.evaluate(expr);
    if (out && typeof out === 'object' && 'href' in out) { this.owner._href = out.href; return out.r; }
    return out;
  }
  // Wait for exactly one match like Playwright strict mode; 0 matches wait.
  async _one(timeout = 15000) {
    const end = Date.now() + timeout;
    let count = 0;
    while (true) {
      count = await this.count();
      if (count === 1) return;
      if (count > 1) throw new Error(`strict mode violation: locator resolved to ${count} elements`);
      if (Date.now() >= end) throw new Error(`locator not found after ${timeout}ms: ${JSON.stringify(this.steps)}`);
      await sleep(200);
    }
  }
  async count() { return this._eval('(els)=>els.length'); }
  async isVisible() {
    const r = await this._eval('(els)=>els.length>1?"__many__":(els.length?__vis(els[0]):false)');
    if (r === '__many__') throw new Error('strict mode violation: isVisible on multiple elements');
    return r;
  }
  async isEnabled() { await this._one(); return this._eval('(els)=>__en(els[0])'); }
  async innerText() { await this._one(); return this._eval('(els)=>els[0].innerText'); }
  async textContent() { await this._one(); return this._eval('(els)=>els[0].textContent'); }
  async inputValue() { await this._one(); return this._eval('(els)=>els[0].value'); }
  async getAttribute(name) { await this._one(); return this._eval('(els,n)=>els[0].getAttribute(n)', name); }
  async evaluate(fn, arg) { await this._one(); return this._eval(`(els,a)=>(${__sourceOf(fn)})(els[0],a)`, arg); }
  async evaluateAll(fn, arg) { return this._eval(`(els,a)=>(${__sourceOf(fn)})(els,a)`, arg); }
  async scrollIntoViewIfNeeded() { await this._one(); await this._eval('(els)=>{els[0].scrollIntoView({block:"center"});return true;}'); }
  // Mark the single match so ego-browser's own actionable-element pipeline
  // (scrolling, hit testing, real input events) performs the action.
  async _target() {
    await this._one();
    const token = 'e' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    await this._eval('(els,t)=>{document.querySelectorAll("[data-ego-target]").forEach(e=>e.removeAttribute("data-ego-target"));els[0].setAttribute("data-ego-target",t);return true;}', token);
    return `[data-ego-target="${token}"]`;
  }
  // Studio's Polymer controls act on DOM click events.  A synthesized CDP
  // mouse event can time out on a busy Studio tab, and a timed-out click is
  // ambiguous (it may have landed), so clicks are dispatched in the page:
  // one call, one click, with a definite result.
  async click() {
    await this._one();
    await this._eval('(els)=>{const e=els[0];e.scrollIntoView({block:"center",inline:"center"});e.click();if(e.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName))e.focus();return true;}');
    await this.owner._refresh();
  }
  async focus() { const sel = await this._target(); await this.owner.raw.focus(sel); }
  async fill(value) { const sel = await this._target(); await this.owner.raw.fill(sel, String(value)); }
  async press(key) { const sel = await this._target(); await this.owner.raw.press(sel, key); }
  // Typing into Studio's contenteditable: text is inserted line by line with a
  // real Enter between lines so the editor keeps the line breaks.
  async pressSequentially(text) {
    const lines = String(text).split(/\r\n?|\n/);
    for (let i = 0; i < lines.length; i++) {
      if (i) await this.owner.raw.keyboard.press('Enter');
      if (lines[i]) await this.owner.raw.keyboard.insertText(lines[i]);
    }
  }
  async type(text) { return this.pressSequentially(text); }
  async setInputFiles(files) {
    const fs = await import('node:fs/promises');
    const list = [];
    for (const file of (Array.isArray(files) ? files : [files])) {
      if (typeof file === 'string') {
        list.push(__path.isAbsolute(file) ? file : __path.resolve(__egoConfig.cwd, file));
      } else if (file && typeof file.name === 'string' && file.buffer) {
        const dir = __path.join(__egoConfig.cwd, 'ego-upload-' + Date.now().toString(36));
        await fs.mkdir(dir, { recursive: true });
        const target = __path.join(dir, __path.basename(file.name));
        await fs.writeFile(target, file.buffer);
        list.push(target);
      } else {
        throw new Error('unsupported setInputFiles entry');
      }
    }
    const sel = await this._target();
    await this.owner.raw.setInputFiles(sel, list);
  }
}

function __textStep(text, options) {
  if (text instanceof RegExp) return { kind: 'text', regex: { source: text.source, flags: text.flags } };
  return { kind: 'text', text: String(text), exact: !!options.exact };
}

class __Page {
  constructor(raw, href) { this.raw = raw; this._href = href || ''; this.keyboard = new __Keyboard(this); }
  url() { return this._href; }
  async _refresh() { try { this._href = await this.raw.url(); } catch (_) {} }
  locator(selector) { return new __Locator(this, [{ kind: 'css', selector }]); }
  getByText(text, options = {}) { return new __Locator(this, [__textStep(text, options)]); }
  async evaluate(fn, arg) {
    const expr = `(async()=>{const __r=await (${__sourceOf(fn)})(${__json(arg)});return {r:__r,href:location.href};})()`;
    const out = await this.raw.evaluate(expr);
    if (out && typeof out === 'object' && 'href' in out) { this._href = out.href; return out.r; }
    return out;
  }
  async title() { return this.raw.title(); }
  async goto(url) { await this.raw.goto(url); await this._refresh(); }
  async reload() { await this.raw.reload(); await this._refresh(); }
  async close() { await this.raw.close(); }
  // Playwright returns the PNG bytes; ego-browser writes a file.
  async screenshot(options = {}) {
    const fs = await import('node:fs/promises');
    const target = options.path || __path.join(__egoConfig.cwd, `ego-shot-${Date.now().toString(36)}.png`);
    await this.raw.screenshot({ path: target });
    const bytes = await fs.readFile(target);
    if (!options.path) await fs.rm(target, { force: true });
    return bytes;
  }
}

// Aside JS_COMMON helpers.  ego-browser resolves frames itself, so the page is
// the only context.
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
};
async function contextsFor(p) { return [p]; }
async function findContext(p, selector) {
  try {
    const loc = p.locator(selector);
    const count = await loc.count();
    for (let i = 0; i < count; i++) {
      const item = loc.nth(i);
      if (await item.isVisible()) return { ctx: p, loc: item };
    }
  } catch (_) {}
  return null;
}
async function findAnyContext(p, selector) {
  try {
    const loc = p.locator(selector);
    if (await loc.count()) return { ctx: p, loc: loc.nth(0) };
  } catch (_) {}
  return null;
}
async function waitForContext(p, selector, timeoutMs = 20000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const found = await findContext(p, selector);
    if (found) return found;
    await sleep(500);
  }
  return null;
}

const openTab = async url => {
  const raw = await __task.newPage();
  await raw.goto(url);
  try { await raw.waitForLoadState('domcontentloaded'); } catch (_) {}
  const page = new __Page(raw);
  await page._refresh();
  return page;
};

try {
  await (async () => {
__YT_EGO_BODY__
  })();
} catch (error) {
  emit({ status: 'error', message: 'ego runtime: ' + String(error?.message || error) });
}
