// ticketdesk · "Needs you", ETA clock and Journal.
//   Needs you  every pending proposal and open ask (an agent's question that needs your decision), newest first, plus
//              tickets whose ETA clock is more than 1.5x over. Approve / reject / answer right there. Polls
//              /api/attention every 5 s; when something new arrives the page chimes (🔔 toggle) and shows a notification.
//   ETA        a ticket can carry "about N minutes left"; the clock starts when it is set and counts down on the page.
//   Journal    one plain-language entry per finished piece of work (story / why / goal / result / next).
// Loaded after sessions.js; uses the main script's globals (DB, byId, MODE, VIEW, t, esc, api, toast, go, body, inline …).
"use strict";
Object.assign(I18N.en, {
  tab_N:"Needs you", tab_J:"Journal",
  n_hint:"Everything waiting on you: proposals to approve, questions agents asked you, and tickets running well over their estimate. Answers go straight back to the session that asked (typed into it when it is idle, otherwise delivered by the inbox hook).",
  n_none:"Nothing is waiting on you.", n_asks:"Questions for you", n_props:"Proposals to approve", n_over:"Over their estimate",
  n_from:"asked by %s", n_answer:"Answer", n_withdraw:"Withdraw", n_text_ph:"Your answer, or a few words with the choice (Ctrl+Enter)",
  n_open:"Open", n_answered:"Answered %s · %s", n_bell_on:"🔔 chime on", n_bell_off:"🔕 chime off", n_new:"New: %s",
  dl_typed:"typed into the session", dl_queued:"queued; delivered when the session next runs", dl_failed:"not delivered: %s",
  foot_N:"Agents ask with: desk.py ask --title … --options \"A|B\" --session <id>", foot_J:"Agents write with: desk.py jadd --title … --result …",
  eta_set:"⏱ ETA", eta_prompt:"About how many minutes are left? (0 clears; the clock starts now)", eta_left:"⏱ %s left", eta_over:"⏱ over %s",
  j_none:"No journal entries yet.", j_hint:"One entry per finished piece of work, in plain words. **Bold** marks a conclusion that holds.",
  j_story:"Story", j_why:"Why", j_goal:"Goal", j_result:"Result", j_next:"Next", j_tickets:"Tickets", j_go:"Go to session", j_del:"Delete",
});
Object.assign(I18N.zh, {
  tab_N:"待我", tab_J:"流水账",
  n_hint:"所有等你的事：待批准的提案、agent 问你的问题、超时 1.5 倍以上的 ticket。回答会直接送回提问的对话（它空闲就打进终端，在跑就由收件箱钩子送达）。",
  n_none:"现在没有等你的事。", n_asks:"问你的问题", n_props:"待批准的提案", n_over:"超出预计时间",
  n_from:"提问方 %s", n_answer:"回答", n_withdraw:"撤回", n_text_ph:"写几句回答，或给选项补一句（Ctrl+Enter）",
  n_open:"打开", n_answered:"已于 %s 回答 · %s", n_bell_on:"🔔 响铃开", n_bell_off:"🔕 响铃关", n_new:"新：%s",
  dl_typed:"已打进对话", dl_queued:"已排队，对话下次运行时送达", dl_failed:"没送到：%s",
  foot_N:"agent 这样问：desk.py ask --title … --options \"A|B\" --session <id>", foot_J:"agent 这样写：desk.py jadd --title … --result …",
  eta_set:"⏱ 预计", eta_prompt:"预计还要多少分钟？（0 清除；从现在开始计时）", eta_left:"⏱ 还剩 %s", eta_over:"⏱ 超 %s",
  j_none:"还没有流水账。", j_hint:"每件事告一段落写一条，用小白话。**加粗** 标出成立的结论。",
  j_story:"来龙去脉", j_why:"动机", j_goal:"目的", j_result:"成果", j_next:"下一步", j_tickets:"涉及", j_go:"切到对话", j_del:"删除",
});
STS.N = []; STS.J = [];

// ───────── tabs
{
  const tabs = document.querySelector(".tabs");
  tabs.insertAdjacentHTML("afterbegin", `<span class="tab" data-mode="N"><span data-i="tab_N"></span><span class="n" id="n_N"></span></span>`);
  tabs.insertAdjacentHTML("beforeend", `<span class="tab" data-mode="J"><span data-i="tab_J"></span><span class="n" id="n_J"></span></span>`);
  for(const m of ["N", "J"]) document.querySelector(`.tab[data-mode="${m}"]`).onclick = async ()=>{
    MODE = m; VIEW = null; history.pushState(null, "", location.pathname + location.search);
    if(m === "N") await loadAtt(); renderChips(); renderTree(); renderPane(); };
}
const xShow = on => { $("#stchips").style.display = $("#prchips").style.display = $("#tagsel").parentNode.style.display = on ? "" : "none"; };
const _xChips = renderChips, _xTree = renderTree, _xPane = renderPane;
renderChips = function(){
  if(MODE !== "N" && MODE !== "J"){ xShow(true); $("#q").style.display = ""; _xChips(); return xCounts(); }
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x.dataset.mode === MODE));
  xShow(false); $("#q").style.display = MODE === "J" ? "" : "none";
  $("#rfoot").textContent = t("foot_" + MODE); xCounts();
};
renderTree = function(){ return MODE === "N" ? needsTree() : MODE === "J" ? journalTree() : _xTree(); };
renderPane = function(){
  if(!VIEW && MODE === "N") return needsPane();
  if(!VIEW && MODE === "J") return journalPane();
  _xPane(); etaDecorate();
};
function xCounts(){
  const n = $("#n_N"); if(n) n.textContent = (ATT.n + ATT.overdue.length) || "";
  const j = $("#n_J"); if(j) j.textContent = (DB.journal || []).length || "";
  document.title = (ATT.n ? `(${ATT.n}) ` : "") + "ticketdesk";
}

// ───────── needs you
let ATT = {items: [], overdue: [], n: 0, sig: ""}, ATT_SEEN = null, ADRAFT = {};
let BELL = lsGet("td_bell", true), ACTX = null;
document.addEventListener("pointerdown", () => { if(!ACTX){ try{ ACTX = new (window.AudioContext || window.webkitAudioContext)(); }catch(e){} } }, {once: true});
function chime(){
  if(!BELL || !ACTX) return;
  try{ for(const [f, at] of [[880, 0], [1320, 0.16]]){ const o = ACTX.createOscillator(), g = ACTX.createGain(); o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, ACTX.currentTime + at); g.gain.exponentialRampToValueAtTime(0.25, ACTX.currentTime + at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ACTX.currentTime + at + 0.3); o.connect(g).connect(ACTX.destination); o.start(ACTX.currentTime + at); o.stop(ACTX.currentTime + at + 0.32); } }catch(e){}
}
async function loadAtt(){
  try{ ATT = await api("/api/attention"); }catch(e){ return; }
  const ids = ATT.items.map(i => i.id);
  if(ATT_SEEN !== null){
    const fresh = ATT.items.filter(i => !ATT_SEEN.includes(i.id));
    if(fresh.length){ chime(); toast(t("n_new", fresh.map(i => i.id + " " + i.title).join(" · ")));
      try{ if(BELL && window.Notification && Notification.permission === "granted") new Notification("ticketdesk: " + fresh[0].title, {body: fresh[0].summary || ""}); }catch(e){} }
  }
  ATT_SEEN = ids; xCounts();
}
const typing = () => !!document.querySelector("#pane textarea:focus, #pane input:focus");
setInterval(async () => { await loadAtt(); if(MODE === "N" && !VIEW && !typing()){ renderTree(); renderPane(); } }, 5000);
loadAtt();

function needsTree(){
  const g = (lab, xs, f) => xs.length ? `<div class="grp">${t(lab)} · ${xs.length}</div>` + xs.map(f).join("") : "";
  const node = (id, title, extra, st) => `<div class="node" data-nid="${id}" data-st="${st}" style="padding-left:6px" title="${esc(id + " " + title)}"><span class="sd"></span><span class="nid">${id}</span><span class="nt">${esc(title)}</span>${extra ? `<span class="cnt">${esc(extra)}</span>` : ""}</div>`;
  const asks = ATT.items.filter(i => i.kind === "ask"), props = ATT.items.filter(i => i.kind === "proposal");
  const h = g("n_asks", asks, i => node(i.id, i.title, i.priority, "open")) + g("n_props", props, i => node(i.id, i.title, i.priority, "pending")) +
    g("n_over", ATT.overdue, o => node(o.id, o.title, "+" + o.over_min + "m", "partial"));
  $("#tree").innerHTML = h || `<div class="empty" style="padding:30px 0">${t("n_none")}</div>`;
}
function askCard(i){
  const opts = (i.options || []).map(o => `<button class="btn" data-na="choose" data-id="${i.id}" data-choice="${esc(o)}">${esc(o)}</button>`).join(" ");
  return `<div class="box ncard" id="nc_${i.id}" data-st="open"><h3>${priB(i.priority)} ${i.id} · ${i.from_name ? esc(t("n_from", i.from_name)) : ""} <span class="hint">${dt(i.created)}</span></h3>`+
    `<div class="atitle">${inline(i.title)}</div>${i.summary ? `<div class="bd">${body(i.summary)}</div>` : ""}`+
    `${(i.tickets || []).length ? `<div class="hint">${i.tickets.map(refHtml).join(" ")}</div>` : ""}`+
    `${opts ? `<div class="nopts">${opts}</div>` : ""}`+
    `<div class="wr"><textarea id="na_${i.id}" rows="2" placeholder="${esc(t("n_text_ph"))}">${esc(ADRAFT[i.id] || "")}</textarea>`+
    `<div style="display:flex;flex-direction:column;gap:4px"><button class="btn main" data-na="answer" data-id="${i.id}">${t("n_answer")}</button><button class="btn" data-na="withdraw" data-id="${i.id}">${t("n_withdraw")}</button></div></div></div>`;
}
function propCard(i){
  return `<div class="box ncard" id="nc_${i.id}" data-st="pending"><h3>${priB(i.priority)} ${i.id} <span class="hint">${dt(i.created)}</span></h3>`+
    `<div class="atitle">${inline(i.title)}</div>${i.summary ? `<div class="bd">${body(i.summary)}</div>` : ""}`+
    `${(i.tickets || []).length ? `<div class="hint">${i.tickets.map(refHtml).join(" ")}</div>` : ""}`+
    `${i.approve_cmd ? `<div class="hint"><code>${esc(i.approve_cmd)}</code></div>` : ""}`+
    `<div style="margin-top:8px"><button class="btn main" data-na="approve" data-id="${i.id}">${i.runnable ? t("approve_run") : t("approve")}</button> `+
    `<button class="btn" data-na="reject" data-id="${i.id}">${t("reject")}</button> <button class="btn" data-go="${i.id}">${t("n_open")}</button></div></div>`;
}
function needsPane(){
  const asks = ATT.items.filter(i => i.kind === "ask"), props = ATT.items.filter(i => i.kind === "proposal");
  const done = (DB.asks || []).filter(a => a.status === "answered").slice(-5).reverse();
  $("#pane").innerHTML = `<div class="home"><h2>${t("tab_N")} <button class="btn sm" id="n_bell">${t(BELL ? "n_bell_on" : "n_bell_off")}</button></h2><div class="hint">${t("n_hint")}</div>`+
    (asks.length || props.length || ATT.overdue.length ? "" : `<div class="box hint">${t("n_none")}</div>`)+
    asks.map(askCard).join("") + props.map(propCard).join("")+
    (ATT.overdue.length ? `<div class="box"><h3>${t("n_over")}</h3>${rows(ATT.overdue.filter(o => byId[o.id]).map(o => byId[o.id]))}</div>` : "")+
    (done.length ? `<div class="box"><h3>${t("st_answered")}</h3>${done.map(a => `<div class="hint">${a.id} ${esc(a.title)} → <b>${esc(a.choice || "")}</b> ${esc(a.answer || "")} · ${dlText(a.delivered)}</div>`).join("")}</div>` : "")+
    `</div>`;
}
function dlText(d){ if(!d) return ""; return d.ok ? t(d.mode === "typed" ? "dl_typed" : "dl_queued") : t("dl_failed", esc(d.why || "?")); }
document.addEventListener("input", e => { if(e.target.id && e.target.id.startsWith("na_")) ADRAFT[e.target.id.slice(3)] = e.target.value; });
document.addEventListener("keydown", e => { if(e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.id && e.target.id.startsWith("na_")){ e.preventDefault(); e.stopPropagation(); answer(e.target.id.slice(3), ""); } }, true);
async function answer(id, choice){
  const text = (ADRAFT[id] || "").trim();
  try{
    const a = await api(`/api/asks/${id}/answer`, {choice, text, via: "page"}); delete ADRAFT[id];
    toast(id + " · " + dlText(a.delivered)); await reload(); await loadAtt(); renderTree(); renderPane();
  }catch(err){ toast(err.message); }
}
document.addEventListener("click", async e => {
  const n = e.target.closest("[data-nid]");
  if(n){ const c = document.getElementById("nc_" + n.dataset.nid); if(c){ c.scrollIntoView({block: "start", behavior: "smooth"}); c.classList.add("flash"); setTimeout(() => c.classList.remove("flash"), 1500); } else go(n.dataset.nid); return; }
  if(e.target.id === "n_bell"){ BELL = !BELL; lsSet("td_bell", BELL); if(BELL && window.Notification && Notification.permission === "default") Notification.requestPermission(); chime(); return renderPane(); }
  const b = e.target.closest("[data-na]"); if(!b) return;
  e.stopPropagation(); const id = b.dataset.id, act = b.dataset.na;
  try{
    if(act === "choose") return answer(id, b.dataset.choice);
    if(act === "answer") return answer(id, "");
    if(act === "withdraw"){ await api(`/api/asks/${id}/withdraw`, {}); toast(id + " · " + t("n_withdraw")); }
    if(act === "approve"){ await api(`/api/proposals/${id}/run`, {by: "page · needs you"}); toast(t("approved_x", id)); }
    if(act === "reject"){ const r = window.prompt(t("reject_reason"), ""); if(r === null) return; await api(`/api/proposals/${id}/reject`, {reason: r}); toast(t("rejected_x", id)); }
    await reload(); await loadAtt(); renderTree(); renderPane();
  }catch(err){ toast(err.message); }
}, true);

// ───────── ETA clock (ticket page)
const fmtDur = s => { s = Math.abs(Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`; };
function etaText(e){ const left = e.min * 60 - (Date.now() / 1000 - e.start); return {over: left < 0, txt: t(left < 0 ? "eta_over" : "eta_left", fmtDur(left))}; }
function etaDecorate(){
  const x = VIEW && byId[VIEW]; if(!x || x.id[0] !== "T") return;
  const ph = document.querySelector("#pane .ph"); if(!ph || ph.querySelector(".etap")) return;
  const pill = document.createElement("span"); pill.className = "pill click plain etap"; pill.dataset.id = x.id;
  if(x.eta && x.status !== "answered"){ const r = etaText(x.eta); pill.textContent = r.txt; pill.classList.toggle("over", r.over); } else pill.textContent = t("eta_set");
  pill.title = t("eta_prompt"); ph.insertBefore(pill, ph.querySelector("h2"));
}
setInterval(() => { const p = document.querySelector(".etap"), x = p && byId[p.dataset.id]; if(x && x.eta && x.status !== "answered"){ const r = etaText(x.eta); p.textContent = r.txt; p.classList.toggle("over", r.over); } }, 1000);
document.addEventListener("click", async e => {
  const p = e.target.closest(".etap"); if(!p) return;
  const v = window.prompt(t("eta_prompt"), ""); if(v === null || v.trim() === "") return;
  try{ await api(`/api/tickets/${p.dataset.id}`, {eta: Number(v) || null}); await reload(); }catch(err){ toast(err.message); }
});

// ───────── journal
const jHl = s => inline(s).replace(/\*\*([^*\n]+)\*\*/g, '<mark class="jhl">$1</mark>');
const jList = () => (DB.journal || []).filter(j => !F.q || JSON.stringify(j).toLowerCase().includes(F.q.toLowerCase())).slice().reverse();
const jWhen = j => esc((j.start ? j.start + " – " : "") + (j.end || String(j.created || "").replace("T", " ").slice(5, 16)));
function journalTree(){
  const xs = jList(); let h = "", day = "";
  for(const j of xs){ const d = String(j.created || "").slice(0, 10); if(d !== day){ day = d; h += `<div class="grp">${esc(d)}</div>`; }
    h += `<div class="node" data-jid="${j.id}" style="padding-left:6px" title="${esc(j.result || "")}"><span class="nid">${j.id}</span><span class="nt">${hl(esc(j.title))}</span><span class="cnt">${esc(j.name || "")}</span></div>`; }
  $("#tree").innerHTML = h || `<div class="empty" style="padding:30px 0">${t("j_none")}</div>`;
}
function journalPane(){
  const live = new Set(((typeof SESS !== "undefined" && SESS.rows) || []).map(r => r.sid));
  const f = (k, v) => v ? `<div class="jf"><span class="jk">${t(k)}</span><span class="jv">${jHl(v)}</span></div>` : "";
  $("#pane").innerHTML = `<div class="home"><h2>${t("tab_J")}</h2><div class="hint">${t("j_hint")}</div>` + (jList().map(j =>
    `<div class="box jcard" id="jc_${j.id}"><h3>${j.id} · ${jWhen(j)} · ${esc(j.name || "")}${j.session ? ` <span class="hint">${esc(j.session.slice(0, 8))}</span>` : ""}`+
    `<span style="flex:1"></span>${j.session && live.has(j.session) ? `<button class="btn sm" data-sk="focus" data-sid="${esc(j.session)}">${t("j_go")}</button>` : ""}`+
    `<button class="btn sm" data-jdel="${j.id}">${t("j_del")}</button></h3><div class="atitle">${hl(jHl(j.title))}</div>`+
    f("j_story", j.story) + f("j_tickets", j.tickets) + f("j_why", j.why) + f("j_goal", j.goal) + f("j_result", j.result) + f("j_next", j.next) + `</div>`).join("")
    || `<div class="box hint">${t("j_none")}</div>`) + `</div>`;
}
document.addEventListener("click", async e => {
  const n = e.target.closest("[data-jid]");
  if(n){ const c = document.getElementById("jc_" + n.dataset.jid); if(c){ c.scrollIntoView({block: "start", behavior: "smooth"}); c.classList.add("flash"); setTimeout(() => c.classList.remove("flash"), 1500); } return; }
  const d = e.target.closest("[data-jdel]"); if(!d) return;
  if(d.dataset.armed !== "1"){ d.dataset.armed = "1"; d.textContent = t("del_confirm"); return; }
  try{ await api(`/api/journal/${d.dataset.jdel}/delete`, {}); toast(t("deleted")); await reload(); }catch(err){ toast(err.message); }
});

document.head.insertAdjacentHTML("beforeend", `<style>
.tabs{flex-wrap:wrap} .tab{flex:1 1 auto}
.tab[data-mode="N"] .n:not(:empty){background:var(--pink);color:#fff;border-radius:8px;padding:0 5px;opacity:1}
.ncard h3,.jcard h3{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.ncard.flash,.jcard.flash{outline:2px solid var(--pink)}
.nopts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.etap.over{background:#fde2e2;color:#b4233c;border-color:#e9a3a3}
.jf{display:flex;gap:10px;margin-top:4px;font-size:13.5px} .jk{flex:none;width:5.5em;color:var(--muted)} .jv{flex:1;min-width:0}
mark.jhl{background:var(--blue-soft);color:var(--blue-ink);font-weight:600;border-radius:4px;padding:0 3px}
</style>`);
renderChips();
