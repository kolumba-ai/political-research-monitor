// Real-browser, read-only smoke test. Requires the local API and its saved Mexico graph.
// Node 24 supplies WebSocket; Chrome supplies CDP. No browser-test package is needed.
import { spawn } from 'node:child_process';
import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';

const origin = process.env.EXPLORER_ORIGIN || 'http://127.0.0.1:8000';
const output = resolve(process.env.EXPLORER_SCREENSHOTS || '../var/explorer-checks');
const profile = await mkdtemp(join(tmpdir(), 'political-explorer-'));
const browser = spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome', [
  '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] });
let socket;
let evaluate;
let shot;
const errors = [];
const failures = [];
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
try {
  const address = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Chrome did not start')), 15000);
    let stderr = '';
    browser.stderr.on('data', (chunk) => {
      stderr += String(chunk);
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    browser.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Chrome exited: ${code}`)); });
    browser.once('error', reject);
  });
  socket = new WebSocket(address);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let sequence = 0;
  const pending = new Map();
  let session;
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id) {
      const promise = pending.get(message.id);
      if (promise) { pending.delete(message.id); if (message.error) promise.reject(new Error(JSON.stringify(message.error))); else promise.resolve(message.result); }
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(message.params.args.map((arg) => arg.value || arg.description || '').join(' '));
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    if (message.method === 'Network.responseReceived' && message.params.response.status >= 400) failures.push(`${message.params.response.status} ${message.params.response.url}`);
  });
  const send = (method, params = {}, targetSession = session) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params, ...(targetSession ? { sessionId: targetSession } : {}) }));
  });
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  session = (await send('Target.attachToTarget', { targetId, flatten: true })).sessionId;
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  const until = async (expression, label, timeout = 20000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) { if (await evaluate(`Boolean(${expression})`)) return; await pause(200); }
    throw new Error(`Timed out: ${label}`);
  };
  shot = async (name) => {
    await mkdir(output, { recursive: true });
    const image = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(join(output, `${name}.png`), Buffer.from(image.data, 'base64'));
  };
  const navigate = async (path) => { await send('Page.navigate', { url: `${origin}${path}` }); await until('document.readyState === "complete" && document.querySelector("header")', 'application load'); };
  const clickText = async (text, selector = 'button') => {
    const expression = `(() => { const b=[...document.querySelectorAll(${JSON.stringify(selector)})].find(b=>b.textContent.includes(${JSON.stringify(text)})); if(!b) return false; b.click(); return true; })()`;
    assert.ok(await evaluate(expression), `Button missing: ${text}`);
  };
  const clickChild = async (text) => {
    for (let page = 0; page < 30; page++) {
      if (await evaluate(`Array.from(document.querySelectorAll('[aria-label="Selected record details"] button')).some(b=>b.textContent.includes(${JSON.stringify(text)}))`)) {
        await clickText(text, '[aria-label="Selected record details"] button'); return;
      }
      const next = await evaluate(`(() => { const b=document.querySelector('[aria-label="Next records"]'); if(!b || b.disabled)return false; b.click(); return true; })()`);
      assert.ok(next, `Child record missing: ${text}`); await pause(250);
    }
    throw new Error(`Could not page to child: ${text}`);
  };
  const selectedRecord = '[aria-label="Selected record details"]';
  const recordTitle = `${selectedRecord} h3`;
  const waitTitle = async (name) => until(`document.querySelector(${JSON.stringify(recordTitle)})?.textContent === ${JSON.stringify(name)}`, `selected record ${name}`);
  const get = async (path) => { const response = await fetch(`${origin}${path}`); assert.ok(response.ok, `API ${path}`); return response.json(); };
  const clickGraphNode = async (id) => {
    await until(`document.querySelector('[role="img"][aria-label^="Interactive government"]')?.dataset.layoutReady === 'true'`, 'graph layout');
    const point = await evaluate(`(() => { const el=document.querySelector('[role="img"][aria-label^="Interactive government"]'); const cy=el?._cyreg?.cy; const pos=cy.getElementById(${JSON.stringify(id)}).renderedPosition(); const rect=el.getBoundingClientRect(); return {x:rect.x+pos.x,y:rect.y+pos.y}; })()`);
    await send('Input.dispatchMouseEvent', { type:'mousePressed', ...point, button:'left', clickCount:1 });
    await send('Input.dispatchMouseEvent', { type:'mouseReleased', ...point, button:'left', clickCount:1 });
  };
  const children = (id) => get(`/api/nodes/${encodeURIComponent(id)}/children`);

  await navigate('/explorer/');
  await until('document.querySelector("[aria-label=Globe] canvas")?.width > 0', 'Earth WebGL canvas');
  await pause(1500);
  await shot('01-earth'); console.log('PASS Earth canvas');

  // Search is driven through React's controlled input, just as a user would type.
  await evaluate(`(() => { const input=document.querySelector('[role="combobox"]'); input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Mexico'); input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await until('!!document.querySelector("[role=option]")', 'country search');
  await evaluate('document.querySelector("[role=option]").dispatchEvent(new MouseEvent("mousedown",{bubbles:true})); document.querySelector("[role=option]")?.click()');
  await until(`document.querySelector('[aria-label="Country information"]')?.textContent.includes("Data coverage")`, 'Mexico data');
  await clickText('Explore government structure', '[aria-label="Country information"] button');
  await until('!!document.querySelector("[data-testid=government-graph] canvas")', 'native graph canvas');
  await waitTitle('Mexico');
  assert.equal(await evaluate('document.querySelectorAll("iframe").length'), 0, 'Native graph must replace the iframe');
  await until(`(() => { const el=document.querySelector('[role="img"][aria-label^="Interactive government"]'); return el?.getBoundingClientRect().height > 200 && el?.dataset.layoutReady === 'true' && Number(el.dataset.nodeCount) >= 3; })()`, 'visible, laid out graph nodes');
  await shot('02-mexico-structure'); console.log('PASS country search and native graph');

  const country = await children('country:MX');
  const tiers = country.children.filter((c) => c.node.label === 'GovernmentLevel');
  const federal = tiers.find((c) => c.node.id.endsWith(':national')).node;
  const subdivisions = tiers.find((c) => c.node.id !== federal.id).node;
  await clickGraphNode(federal.id); await waitTitle(federal.name);
  const federalChildren = await children(federal.id);
  const presidency = federalChildren.children.find((c) => /presiden/i.test(c.node.name) && c.node.label === 'GovernmentBody').node;
  await clickChild(presidency.name); await waitTitle(presidency.name);
  const presidencyChildren = await children(presidency.id);
  const office = presidencyChildren.children.find((c) => c.node.label === 'PoliticalOffice' && /president of mexico/i.test(c.node.name)).node;
  await clickChild(office.name); await waitTitle(office.name);
  const officeChildren = await children(office.id);
  const holder = officeChildren.children.find((c) => c.node.label === 'Person').node;
  await clickChild(holder.name); await waitTitle(holder.name);
  const profileText = await evaluate(`document.querySelector(${JSON.stringify(selectedRecord)}).textContent`);
  assert.match(profileText, /Current office/); assert.match(profileText, /Party:/); assert.match(profileText, /Term:/); assert.match(profileText, /Retrieved/);
  assert.ok(await evaluate(`${JSON.stringify(selectedRecord)} && document.querySelectorAll('[aria-label="Selected record details"] a[href^="https://"]').length > 0`), 'Profile source links');
  await shot('03-officeholder-evidence'); console.log('PASS officeholder path and evidence');

  // Shared links restore the person and the real ancestor path after a reload.
  const shareUrl = await evaluate('location.pathname + location.search');
  assert.ok(shareUrl.includes('node=')); await navigate(shareUrl); await waitTitle(holder.name);
  await clickText('Mexico', `${selectedRecord} nav button`); await waitTitle('Mexico');
  await clickText(subdivisions.name, `${selectedRecord} button`); await waitTitle(subdivisions.name);
  await until('document.body.textContent.includes("Records 1–6 of 32")', 'state pagination');
  await evaluate(`document.querySelector('[aria-label="Next records"]').click()`);
  await until('document.body.textContent.includes("Records 7–12 of 32")', 'second state page');
  await shot('04-state-pagination'); console.log('PASS deep link and state paging');

  // Switching sibling branches collapses the previous subtree and retains the country root.
  await evaluate(`(() => { const select=document.querySelector('[aria-label="Browse records at the selected level"]'); select.value=${JSON.stringify(federal.id)}; select.dispatchEvent(new Event('change',{bubbles:true})); })()`);
  await waitTitle(federal.name);
  assert.equal(await evaluate('document.body.textContent.includes("Records 7–12 of 32")'), false);
  await evaluate(`document.querySelector('button[title="Reduce animation"]').click()`);
  assert.equal(await evaluate(`document.querySelector('button[title="Reduce animation"]').getAttribute("aria-pressed")`), 'true');

  await evaluate(`document.querySelector('[aria-label="Reset graph"]').click()`); await waitTitle('Mexico');
  await evaluate(`Array.from(document.querySelectorAll('[data-testid="government-graph"] label')).find(l=>l.textContent.includes('single-source')).querySelector('input').click()`);
  await pause(400); await waitTitle('Mexico');
  await navigate('/explorer/?country=FR');
  await until(`document.querySelector('[aria-label="Country information"]')?.textContent.includes("Government structure is not available yet")`, 'missing government state');
  await shot('05-unresearched-country');
  await evaluate(`(() => { const input=document.querySelector('[role="combobox"]'); input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Sheinbaum'); input.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await until(`Array.from(document.querySelectorAll('[role="option"]')).some(o=>o.textContent.includes(${JSON.stringify(holder.name)}))`, 'entity search');
  await evaluate(`Array.from(document.querySelectorAll('[role="option"]')).find(o=>o.textContent.includes(${JSON.stringify(holder.name)})).dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))`);
  await waitTitle(holder.name);
  assert.equal(await evaluate('new URLSearchParams(location.search).get("country")'), 'MX', 'Entity search selects its country');
  console.log('PASS entity search, root filters and missing data');

  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate(shareUrl);
  await waitTitle(holder.name);
  assert.ok(await evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'No horizontal mobile overflow');
  await shot('06-mobile');

  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `const getContext=HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl2'||type==='webgl'?null:getContext.call(this,type,...args)}` });
  await navigate('/explorer/?country=MX');
  await until('document.querySelector("[aria-label=Globe]")?.textContent.includes("3D view is unavailable")', 'WebGL fallback');
  assert.ok(await evaluate('document.querySelectorAll("[aria-label=Globe] button").length > 200'), 'Accessible country list');
  await shot('07-no-webgl');
  assert.deepEqual(errors, [], 'Browser JavaScript exceptions');
  assert.deepEqual(failures, [], 'HTTP errors');
  console.log(`PASS: Earth, country search, Mexico native drill-down, source profile, deep link, accordion, paging, low motion, missing data, mobile and WebGL fallback. Screenshots: ${output}`);
} catch (error) {
  if (evaluate && shot) {
    await shot('failure').catch(() => {});
    console.error('Browser state:', await evaluate('document.body.innerText.slice(0,2500)').catch(() => 'unavailable'));
  }
  throw error;
} finally {
  socket?.close();
  browser.kill('SIGTERM');
  await pause(500);
  await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }).catch(() => {});
}
