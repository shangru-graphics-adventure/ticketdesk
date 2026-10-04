// ticketdesk · Sessions tab: what each live Claude Code session is working on (including questions you typed
// while it was busy), which tickets that touches, and -- once a turn ends -- the key points plus a box to reply.
// Loaded after the main script in index.html; uses its globals (DB, byId, MODE, VIEW, t, esc, api, toast, go, body …).
"use strict";
Object.assign(I18N.en, {
  tab_S:"Sessions", s_wait:"Waiting for you", s_run:"Running", s_none:"No live Claude Code sessions.",
  s_hint:"Live Claude Code sessions on this machine. Tasks are read from each transcript: your prompts, the questions you typed while it was busy, and the tickets it logged to. When a turn ends you get the key points and can reply here.",
  w_running:"working", w_answered:"answered", w_done:"earlier", via_prompt:"prompt", "via_mid-turn":"mid-turn", via_reply:"reply", via_logged:"logged to",
  w_noticket:"no ticket", w_mk:"Make ticket", w_mk_tip:"Create a ticket from this prompt and link it to the session",
  w_earlier:"Earlier (%d, last 12 h)", w_points:"Key points", src_recap:"Claude Code recap", src_reply:"from the reply", w_full:"Full reply",
  w_answered_ago:"answered %s", w_reply_ph:"Reply to %s (Ctrl+Enter). Typed into its terminal through the VS Code bridge.",
  w_send:"Send", w_focus:"Go to terminal", w_guess:"guessed from a bare number", w_title_prompt:"Title for the new ticket",
  w_sent:"Typed into its terminal", w_made:"Created %s", st_idle:"waiting", st_busy:"running", st_waiting:"needs permission", st_shell:"shell",
  foot_S:"Reply and Go to terminal need the VS Code bridge (integrations/vscode).", ago_s:"%ds ago", ago_m:"%dm ago", ago_h:"%dh ago",
});
Object.assign(I18N.zh, {
  tab_S:"对话", s_wait:"等你", s_run:"在跑", s_none:"没有活着的 Claude Code 对话。",
  s_hint:"本机活着的 Claude Code 对话。任务从对话记录里读：你的提问、它运算时你插的问题、它写过日志的 ticket。一轮结束后显示要点，可以直接在这里回答。",
  w_running:"在算", w_answered:"已答·等你", w_done:"早先", via_prompt:"提问", "via_mid-turn":"插问", via_reply:"回复", via_logged:"写了日志",
  w_noticket:"无 ticket", w_mk:"建 ticket", w_mk_tip:"用这条提问建一张 ticket，并关联到这个对话",
  w_earlier:"更早的 %d 条（12 小时内）", w_points:"要点", src_recap:"Claude Code 回顾", src_reply:"回复摘要", w_full:"完整回复",
  w_answered_ago:"答于 %s", w_reply_ph:"直接回答 %s（Ctrl+Enter），经 VS Code 桥打进它的终端",
  w_send:"发送", w_focus:"切到终端", w_guess:"从裸数字推断", w_title_prompt:"新 ticket 的标题",
  w_sent:"已打进它的终端", w_made:"已建 %s", st_idle:"等你", st_busy:"在跑", st_waiting:"等权限确认", st_shell:"shell",
  foot_S:"「发送」和「切到终端」需要 VS Code 桥（integrations/vscode）。", ago_s:"%d 秒前", ago_m:"%d 分钟前", ago_h:"%d 小时前",
});
STS.S = [];
const mdLinkT = s => s.replace(/(^|[^A-Za-z0-9])([TPK]\d{3,})(?![A-Za-z0-9])/g, (m, pre, id) => pre + refHtml(id));
let SESS = {rows:[]}, SDRAFT = {};
const sWaiting = r => r.status!=="busy" && r.work && !r.work.turn_open;
const sName = r => (r.work && r.work.title ? r.work.title+" · " : "") + (r.name || r.sid.slice(0,8));
function sAgo(ep){ const s=Math.max(0, Date.now()/1000-ep); return s<90?t("ago_s",Math.round(s)):s<5400?t("ago_m",Math.round(s/60)):t("ago_h",Math.round(s/3600)); }
async function loadSessions(){ try{ SESS = await api("/api/sessions"); }catch(e){ SESS = {rows:[], error:e.message}; } const n=$("#n_S"); if(n) n.textContent = SESS.rows.filter(sWaiting).length || ""; }

function sTask(r, x, i){
  const chips = x.refs.map(refHtml).join(" ") + x.guess.map(id=>byId[id]?`<span class="ref guess" data-go="${id}" title="${esc(t("w_guess"))}">${id}?</span>`:"").join(" ");
  const free = !x.refs.length && !x.guess.length && !["logged","reply"].includes(x.via);
  return `<div class="wt ${x.state}"><span class="ws ${x.state}">${t("w_"+x.state)}</span><span class="wv">${t("via_"+x.via)}</span>`+
    `<span class="wx" title="${esc(x.full||"")}">${esc(x.text)}</span>${chips?`<span>${chips}</span>`:""}`+
    (free?`<span class="wn">${t("w_noticket")}</span><button class="btn sm" data-sk="mk" data-sid="${r.sid}" data-i="${i}" title="${esc(t("w_mk_tip"))}">${t("w_mk")}</button>`:"")+`</div>`;
}
function sCard(r){
  const w = r.work || {}, tasks = (w.tasks||[]).map((x,i)=>[x,i]);
  const cur = tasks.filter(([x])=>x.state!=="done"), old = tasks.filter(([x])=>x.state==="done");
  const wait = sWaiting(r);
  let h = `<div class="box scard" data-sid="${r.sid}"><div class="shd"><span class="sdot ${r.status}"></span><b class="slink" data-sk="focus" data-sid="${r.sid}">${esc(sName(r))}</b>`+
    `<span class="tag">${t("st_"+r.status)}</span><span style="flex:1"></span><button class="btn" data-sk="focus" data-sid="${r.sid}">${t("w_focus")}</button></div>`+
    `<div class="hint">${esc(r.cwd)} · ${r.sid}</div>`;
  if(w.error) h += `<div class="hint">${esc(w.error)}</div>`;
  if(cur.length) h += `<div class="wl">${cur.map(([x,i])=>sTask(r,x,i)).join("")}</div>`;
  if(old.length) h += `<details class="wo"><summary>${t("w_earlier", old.length)}</summary>${old.map(([x,i])=>sTask(r,x,i)).join("")}</details>`;
  if(wait && ((w.points||[]).length || w.last_reply)){
    h += `<div class="wp"><div><b>${t("w_points")}</b> <span class="hint">${t("src_"+w.points_src)}${w.last_end?" · "+t("w_answered_ago", sAgo(w.last_end)):""}</span></div>`+
      ((w.points||[]).length?`<ul>${w.points.map(p=>`<li class="md">${mdInline(p, mdLinkT)}</li>`).join("")}</ul>`:"")+
      (w.last_reply?`<details><summary>${t("w_full")}</summary><div class="wb md">${mdRender(w.last_reply, mdLinkT)}</div></details>`:"")+`</div>`;
  }
  if(wait && r.status==="idle") h += `<div class="wr"><textarea rows="2" id="sr_${r.sid}" placeholder="${esc(t("w_reply_ph", r.name||""))}">${esc(SDRAFT[r.sid]||"")}</textarea><button class="btn main" data-sk="send" data-sid="${r.sid}">${t("w_send")}</button></div>`;
  return h + `</div>`;
}
function renderSessionsTree(){
  let h = "";
  for(const [lab, fn] of [["s_wait", sWaiting], ["s_run", r=>!sWaiting(r)]]){
    const xs = SESS.rows.filter(fn); if(!xs.length) continue;
    h += `<div class="grp">${t(lab)} · ${xs.length}</div>` + xs.map(r=>{
      const cur = (r.work?.tasks||[]).filter(x=>x.state!=="done"), ids=[...new Set(cur.flatMap(x=>x.refs))];
      return `<div class="node" data-ssid="${r.sid}" style="padding-left:6px" title="${esc(cur.map(x=>"· "+x.text).join("\n"))}"><span class="sdot ${r.status}"></span>`+
        `<span class="nt">${esc(sName(r))}</span><span class="cnt">${ids.slice(0,3).join(" ")}</span></div>`; }).join("");
  }
  $("#tree").innerHTML = h || `<div class="empty" style="padding:30px 0">${esc(SESS.error || t("s_none"))}</div>`;
}
function renderSessionsPane(){
  $("#pane").innerHTML = `<div class="home"><h2>${t("tab_S")}</h2><div class="hint">${t("s_hint")}</div>${SESS.rows.map(sCard).join("") || `<div class="box hint">${esc(SESS.error || t("s_none"))}</div>`}</div>`;
}
// hook into the main renderers
const _chips = renderChips, _tree = renderTree, _pane = renderPane;
renderChips = function(){
  if(MODE!=="S") { $("#stchips").style.display = $("#q").style.display = $("#tagsel").parentNode.style.display = ""; return _chips(); }
  document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on", x.dataset.mode==="S"));
  $("#stchips").style.display = $("#prchips").style.display = $("#q").style.display = $("#tagsel").parentNode.style.display = "none";
  $("#rfoot").textContent = t("foot_S");
};
renderTree = function(){ return MODE==="S" ? renderSessionsTree() : _tree(); };
renderPane = function(){ return MODE==="S" && !VIEW ? renderSessionsPane() : _pane(); };
const stab = document.querySelector('.tab[data-mode="S"]');
stab.onclick = async ()=>{ MODE="S"; VIEW=null; history.pushState(null,"",location.pathname+location.search); await loadSessions(); renderChips(); renderTree(); renderPane(); };
document.querySelectorAll('.tab:not([data-mode="S"])').forEach(x=>x.addEventListener("click", ()=>{ $("#stchips").style.display = $("#q").style.display = $("#tagsel").parentNode.style.display = ""; }));
setInterval(async ()=>{ await loadSessions(); if(MODE==="S" && !VIEW && !document.querySelector("#pane textarea:focus")){ renderTree(); renderPane(); } }, 15000);
loadSessions();

// actions
async function sSend(sid){
  const ta = document.getElementById("sr_"+sid), text = (ta?.value||"").trim(); if(!text) return;
  try{ await api(`/api/sessions/${sid}/reply`, {text}); ta.value=""; delete SDRAFT[sid]; toast(t("w_sent")); setTimeout(async ()=>{ await loadSessions(); renderTree(); renderPane(); }, 3000); }
  catch(e){ toast(e.message); }
}
document.addEventListener("input", e=>{ if(e.target.id?.startsWith("sr_")) SDRAFT[e.target.id.slice(3)] = e.target.value; });
document.addEventListener("keydown", e=>{ if(e.key==="Enter" && (e.ctrlKey||e.metaKey) && e.target.id?.startsWith("sr_")){ e.preventDefault(); sSend(e.target.id.slice(3)); } }, true);
document.addEventListener("click", async e=>{
  const n = e.target.closest("[data-ssid]");
  if(n){ const c=document.querySelector(`.scard[data-sid="${n.dataset.ssid}"]`); if(c){ c.scrollIntoView({block:"start", behavior:"smooth"}); c.classList.add("flash"); setTimeout(()=>c.classList.remove("flash"),1500); } return; }
  const b = e.target.closest("[data-sk]"); if(!b) return;
  e.stopPropagation(); const sid=b.dataset.sid, r=SESS.rows.find(x=>x.sid===sid);
  try{
    if(b.dataset.sk==="send") return sSend(sid);
    if(b.dataset.sk==="focus"){ await api(`/api/sessions/${sid}/focus`, {}); return; }
    if(b.dataset.sk==="mk"){
      const x = r.work.tasks[+b.dataset.i], title = window.prompt(t("w_title_prompt"), x.text.replace(/…$/,"")); if(!title) return;
      const tk = await api("/api/tickets", {title, question: x.full || x.text});
      await api(`/api/tickets/${tk.id}/log`, {text: "Created from a prompt in session "+(r.name||sid.slice(0,8)), session: sid, name: r.name||""});
      toast(t("w_made", tk.id)); await reload(); await loadSessions(); renderTree(); renderPane();
    }
  }catch(err){ toast(err.message); }
}, true);

document.head.insertAdjacentHTML("beforeend", `<style>
.sdot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--muted);margin-right:4px}
.sdot.idle{background:var(--pink)} .sdot.busy{background:var(--lav)} .sdot.waiting{background:#e0a020}
.scard .shd{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.slink{cursor:pointer;text-decoration:underline dotted;text-underline-offset:3px}
.scard.flash{outline:2px solid var(--pink)}
.wl,.wo{margin-top:6px}
.wt{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:13px;padding:3px 0;border-top:1px dashed var(--line)}
.wl .wt:first-child{border-top:0}
.ws{font-size:11px;border-radius:6px;padding:0 6px;white-space:nowrap}
.ws.running{background:var(--lav-soft);color:var(--lav-ink)} .ws.answered{background:var(--pink-soft);color:var(--pink-ink)} .ws.done{background:var(--hover);color:var(--muted)}
.wv{font-size:11px;color:var(--muted);white-space:nowrap}
.wx{flex:1 1 220px;min-width:0} .wt.done .wx{color:var(--muted)}
.wn{font-size:11px;color:#a0660c;background:#fdf0d8;border-radius:6px;padding:0 6px}
.ref.guess{background:transparent;border:1px dashed var(--lav)}
.btn.sm{font-size:11.5px;padding:1px 7px}
.wo summary{font-size:12px;color:var(--muted);cursor:pointer}
.wp{margin-top:8px;border-left:4px solid var(--blue);background:var(--blue-soft);border-radius:8px;padding:6px 10px}
.wp ul{margin:4px 0 2px 18px;padding:0} .wp li{margin:2px 0}
.wp summary{font-size:12px;cursor:pointer;color:var(--blue-ink)} .wb{max-height:420px;overflow:auto;font-size:13px}
.wr{display:flex;gap:6px;margin-top:6px} .wr textarea{flex:1;min-width:0;font:inherit;border:1px solid var(--line);border-radius:10px;padding:6px;background:var(--panel);color:var(--ink)}
@media (max-width:640px){ .wr{flex-direction:column} }
</style>`);
