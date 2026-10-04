// URLs and file/folder paths in ticket text become links; hovering a path lists clickable parent folders.
// usage: node tests/browser_xref.mjs <desk url> <ticket id whose text has a URL and a path>   (rc=1 on failure)
import { spawn } from 'node:child_process';
const [BASE, TID] = process.argv.slice(2);
const chrome = process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome';
const p = spawn(chrome, ['--headless=new','--disable-gpu','--remote-debugging-port=9359','--user-data-dir='+(process.env.TEMP||'/tmp')+'/cdp_td_xref','--window-size=1300,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9359/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
await send('Runtime.enable'); await send('Page.navigate',{url:BASE+'/?lang=en#'+TID});
for(let i=0;i<60;i++){ if(await ev(`document.querySelectorAll('#pane .xr-path').length>0`)) break; await sleep(100); }
const out={}, bad=[];
out.links = await ev(`({url:[...document.querySelectorAll('#pane a.xr-url')].map(a=>a.href), path:[...document.querySelectorAll('#pane .xr-path')].map(x=>x.textContent)})`);
if(!out.links.url.length) bad.push('no URL link');
if(!out.links.path.length) bad.push('no path link');
out.hover = await ev(`(async()=>{ const el=document.querySelector('#pane .xr-path'); el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
  for(let i=0;i<40;i++){ const t=document.querySelector('.xtip'); if(t&&!t.hidden&&t.textContent!=='…') break; await new Promise(z=>setTimeout(z,100)); }
  const t=document.querySelector('.xtip'); return {text:t.textContent.slice(0,200), parents:t.querySelectorAll('.xpar a').length, preview:!!t.querySelector('a[href^="/file"]')}; })()`);
if(!out.hover.parents) bad.push('hover shows no parent folders');
if(out.hover.preview) bad.push('offers a preview link but this server has no /file');
if(!/Folder|File/.test(out.hover.text)) bad.push('popup not in English with ?lang=en');
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
