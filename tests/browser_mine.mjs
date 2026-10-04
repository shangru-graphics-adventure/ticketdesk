// "My understanding" box on a ticket: saves after you stop typing, survives a reload, and a re-render while typing keeps focus and text.
// usage: node tests/browser_mine.mjs <desk url> <ticket id>   (rc=1 on failure)
import { spawn } from 'node:child_process';
const [BASE, TID] = process.argv.slice(2);
const chrome = process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome';
const p = spawn(chrome, ['--headless=new','--disable-gpu','--remote-debugging-port=9363','--user-data-dir='+(process.env.TEMP||'/tmp')+'/cdp_td_mine','--window-size=1300,900','about:blank']);
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); let ws;
for(let i=0;i<40;i++){ try{ const j=await (await fetch('http://127.0.0.1:9363/json')).json(); const pg=j.find(x=>x.type==='page'); if(pg){ ws=new WebSocket(pg.webSocketDebuggerUrl); break; } }catch(e){} await sleep(250); }
await new Promise(r=>ws.onopen=r); let id=0; const pend={}, errs=[];
ws.onmessage=ev=>{const m=JSON.parse(ev.data); if(m.id&&pend[m.id]){pend[m.id](m);delete pend[m.id];} if(m.method==='Runtime.exceptionThrown') errs.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}));});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,awaitPromise:true,returnByValue:true}); return r.result.result?.value ?? r.result.exceptionDetails?.text;};
const typeKeys=async s=>{ for(const ch of s){ await send('Input.insertText',{text:ch}); await sleep(15); } };
await send('Runtime.enable'); await send('Page.navigate',{url:BASE+'/?lang=en#'+TID});
for(let i=0;i<60;i++){ if(await ev(`!!document.getElementById("mine")`)) break; await sleep(100); }
const out={}, bad=[], word='note'+Date.now()%100000;
await ev(`document.getElementById("mine").focus()`); await typeKeys(word.slice(0,4));
out.before = await ev(`fetch("/api/state").then(r=>r.json()).then(d=>d.tickets.find(x=>x.id==="${TID}").mine||"")`);
if(out.before===word.slice(0,4)) bad.push('saved before typing stopped (debounce not working)');
await ev(`renderTicket(byId["${TID}"])`); await sleep(150); await typeKeys(word.slice(4));
out.focus = await ev(`({focused: document.activeElement && document.activeElement.id==="mine", v: document.getElementById("mine").value})`);
if(!out.focus.focused || out.focus.v!==word) bad.push('re-render lost focus or text');
await sleep(1300);
await send('Page.navigate',{url:BASE+'/?lang=en#'+TID}); for(let i=0;i<60;i++){ if(await ev(`!!document.getElementById("mine")`)) break; await sleep(100); }
out.after = await ev(`document.getElementById("mine").value`);
if(out.after!==word) bad.push('not saved: '+out.after);
out.errs=errs; out.bad=bad; console.log(JSON.stringify(out,null,1)); ws.close(); p.kill(); process.exit(bad.length||errs.length?1:0);
