// Headless check of "Needs you", the ETA clock and the Journal tab (needs Chrome). Seeds its own records on the
// server you point it at, so use a throw-away data dir:
//   python server.py --data ./tmp-data --port 8761 &
//   node tests/browser_needs.mjs http://127.0.0.1:8761/ [screenshot.png]      -> exit 1 on JS errors or any failed check
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const URL = process.argv[2] || 'http://127.0.0.1:8761/', SHOT = process.argv[3];
const CHROME = process.env.CHROME || (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome');
const post = async (path, body) => { const r = await fetch(new globalThis.URL(path, URL), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Desk-Client': '1' }, body: JSON.stringify(body) }); const j = await r.json(); if (!r.ok) throw new Error(path + ' ' + j.error); return j; };
// seed: one ticket with a running ETA and one overdue, one ask with options, one pending proposal, one journal entry
const tk = await post('/api/tickets', { title: 'Why did CI double?' });
const late = await post('/api/tickets', { title: 'Overdue card' });
await post(`/api/tickets/${tk.id}`, { eta: 25 });
await post(`/api/tickets/${late.id}`, { eta: { min: 1, start: Date.now() / 1000 - 600 } });
const ask = await post('/api/asks', { title: 'Pin the cache key?', situation: 'It explains 8 of the extra 10 minutes.', options: 'pin|leave', tickets: tk.id, from_name: 'ci-speed', priority: 'P0' });
const prop = await post('/api/proposals', { title: 'Batch dependency-bot updates', summary: 'Weekly instead of daily', tickets: tk.id, approve_cmd: 'batch.sh' });
await post('/api/journal', { title: 'CI cache investigation', result: '**the lockfile key** explains most of it', tickets: `${tk.id} (CI time)`, story: 'CI went from 10 to 21 minutes.' });

const p = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=9339', '--user-data-dir=' + (process.env.TEMP || '/tmp') + '/td_needs_test', '--window-size=1400,900', 'about:blank']);
const sleep = ms => new Promise(r => setTimeout(r, ms)); let ws;
for (let i = 0; i < 40; i++) { try { const j = await (await fetch('http://127.0.0.1:9339/json')).json(); const pg = j.find(x => x.type === 'page'); if (pg) { ws = new WebSocket(pg.webSocketDebuggerUrl); break; } } catch (e) {} await sleep(250); }
await new Promise(r => ws.onopen = r); let id = 0; const pend = {}, errs = [];
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } if (m.method === 'Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }); return r.result.result?.value ?? r.result.exceptionDetails?.text; };
const until = async (e, ms = 5000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e)) return Date.now() - t0; await sleep(100); } return -1; };
await send('Runtime.enable'); await send('Page.enable'); const o = {}, bad = [];
await send('Page.navigate', { url: URL }); await sleep(1500);

o.badge = await ev(`document.querySelector('#n_N').textContent`);
if (!(+o.badge >= 3)) bad.push('Needs-you badge should count the ask, the proposal and the overdue ticket');
await ev(`document.querySelector('.tab[data-mode="N"]').click()`);
o.cards_ms = await until(`document.querySelectorAll('.ncard').length >= 2`);
if (o.cards_ms < 0) bad.push('Needs-you cards did not render');
o.opts = await ev(`[...document.querySelectorAll('#nc_${ask.id} [data-na=choose]')].map(b => b.textContent).join('|')`);
if (o.opts !== 'pin|leave') bad.push('ask options not shown as buttons: ' + o.opts);
o.overdue_row = await ev(`!!document.querySelector('#pane [data-go="${late.id}"]')`);
if (!o.overdue_row) bad.push('overdue ticket not listed');
o.search_hidden = await ev(`getComputedStyle(document.querySelector('#q')).display === 'none'`);
// answer by clicking an option -> card disappears, ask is answered on the server
await ev(`document.querySelector('#nc_${ask.id} [data-na=choose][data-choice=pin]').click()`);
o.answer_ms = await until(`!document.querySelector('#nc_${ask.id}')`);
const st = await (await fetch(new globalThis.URL('/api/state', URL))).json();
o.ask_status = st.asks.find(a => a.id === ask.id)?.status;
if (o.ask_status !== 'answered' || o.answer_ms < 0) bad.push('clicking an option did not answer the ask');
// proposal approve from the card
await ev(`document.querySelector('#nc_${prop.id} [data-na=approve]').click()`);
o.approve_ms = await until(`!document.querySelector('#nc_${prop.id}')`);
if (o.approve_ms < 0) bad.push('approving from the card did not clear it');
// ETA pill on the ticket page counts down
await ev(`go('${tk.id}')`); await sleep(300);
o.eta1 = await ev(`document.querySelector('.etap')?.textContent`); await sleep(1300);
o.eta2 = await ev(`document.querySelector('.etap')?.textContent`);
if (!o.eta1 || o.eta1 === o.eta2 || !/\d:\d\d/.test(o.eta1)) bad.push('ETA pill missing or not ticking: ' + o.eta1 + ' / ' + o.eta2);
await ev(`go('${late.id}')`); await sleep(300);
o.late_over = await ev(`document.querySelector('.etap')?.classList.contains('over')`);
if (!o.late_over) bad.push('overdue ETA not marked');
// journal
await ev(`document.querySelector('.tab[data-mode="J"]').click()`); await sleep(300);
o.journal_cards = await ev(`document.querySelectorAll('.jcard').length`);
o.journal_hl = await ev(`document.querySelector('.jcard mark.jhl')?.textContent`);
if (o.journal_cards < 1 || o.journal_hl !== 'the lockfile key') bad.push('journal card or **highlight** missing');
o.journal_ref = await ev(`!!document.querySelector('.jcard .ref[data-go="${tk.id}"]')`);
if (!o.journal_ref) bad.push('ticket id in a journal entry is not a link');
// back to Tickets restores the filters
await ev(`document.querySelector('.tab[data-mode="T"]').click()`); await sleep(200);
o.back_ok = await ev(`getComputedStyle(document.querySelector('#stchips')).display !== 'none' && getComputedStyle(document.querySelector('#q')).display !== 'none'`);
if (!o.back_ok) bad.push('switching back to Tickets did not restore the filters');
await ev(`document.querySelector('.tab[data-mode="N"]').click()`); await sleep(300);
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }); await sleep(400);
o.narrow = await ev(`document.documentElement.scrollWidth + '/' + innerWidth`);
const [sw, iw] = o.narrow.split('/').map(Number); if (sw > iw) bad.push('horizontal scroll at 390 px: ' + o.narrow);
await send('Emulation.clearDeviceMetricsOverride'); await sleep(300);
if (SHOT) { const s = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(SHOT, Buffer.from(s.result.data, 'base64')); }
o.errs = errs; o.bad = bad;
console.log(JSON.stringify(o, null, 1)); ws.close(); p.kill();
process.exit(errs.length || bad.length ? 1 : 0);
