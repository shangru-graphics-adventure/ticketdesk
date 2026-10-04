// Headless check of the Sessions tab (needs Chrome and a running server). Renders only; sends nothing.
//   node tests/browser_sessions.mjs [url] [screenshot.png]   -> exit 1 on JS errors or missing elements
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const URL = process.argv[2] || 'http://127.0.0.1:8750/', SHOT = process.argv[3];
const CHROME = process.env.CHROME || (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome');
const p = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=9338', '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/td_sess_test', '--window-size=1400,900', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms)); let ws;
for (let i = 0; i < 40; i++) { try { const j = await (await fetch('http://127.0.0.1:9338/json')).json(); const pg = j.find(x => x.type === 'page'); if (pg) { ws = new WebSocket(pg.webSocketDebuggerUrl); break; } } catch (e) {} await sleep(250); }
await new Promise(r => ws.onopen = r); let id = 0; const pend = {}, errs = [];
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } if (m.method === 'Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }); return r.result.result?.value ?? r.result.exceptionDetails?.text; };
await send('Runtime.enable'); const o = {}, bad = [];
await send('Page.navigate', { url: URL }); await sleep(2000);
const t0 = Date.now(); await ev(`document.querySelector('.tab[data-mode="S"]').click()`);
for (let i = 0; i < 50 && !(await ev(`document.querySelectorAll('.scard').length`)); i++) await sleep(100);
o.ms = Date.now() - t0;
o.cards = await ev(`document.querySelectorAll('.scard').length`);
o.tasks = await ev(`document.querySelectorAll('.wt').length`);
o.points = await ev(`document.querySelectorAll('.wp').length`);
o.reply_boxes = await ev(`document.querySelectorAll('textarea[id^=sr_]').length`);
o.tree = await ev(`document.querySelectorAll('#tree [data-ssid]').length`);
o.other_tab_ok = await ev(`(document.querySelector('.tab[data-mode="T"]').click(), document.querySelectorAll('#tree .node').length >= 0 && getComputedStyle(document.querySelector('#q')).display !== 'none')`);
await ev(`document.querySelector('.tab[data-mode="S"]').click()`); await sleep(800);
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }); await sleep(400);
o.narrow_hscroll = await ev(`document.documentElement.scrollWidth + '/' + innerWidth`);
await send('Emulation.clearDeviceMetricsOverride'); await sleep(400);
if (SHOT) { const s = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(SHOT, Buffer.from(s.result.data, 'base64')); }
o.errs = errs;
if (!o.other_tab_ok) bad.push('switching back to Tickets did not restore the search box');
o.bad = bad; console.log(JSON.stringify(o, null, 1)); ws.close(); p.kill();
process.exit(errs.length || bad.length ? 1 : 0);
