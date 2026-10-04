// ticketdesk terminal bridge: lets the ticketdesk Sessions tab reach a specific VS Code terminal tab.
//
//   GET  /ping                     who am I
//   GET  /terminals                [{pid, name, active}]
//   POST /show {pid}               bring that terminal into view
//   POST /type {pid, text}         type one line + Enter (text must start with 【, single line, <= 4000 chars)
//
// Terminal.processId is the pid of the shell in the tab, i.e. the parent of the claude process, which is how
// ticketdesk finds the right tab. Binds 127.0.0.1 on the first free port in 8721-8728 (one per VS Code window)
// and refuses any request that carries an Origin header, so web pages cannot drive it.
const vscode = require("vscode");
const http = require("http");
let server = null;

function json(res, code, obj) {
  const body = Buffer.from(JSON.stringify(obj), "utf8");
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Content-Length": body.length, "Cache-Control": "no-store" });
  res.end(body);
}
async function findByPid(pid) {
  for (const t of vscode.window.terminals) {
    try { if ((await t.processId) === pid) return t; } catch (e) { /* skip */ }
  }
  return null;
}
function readBody(req) {
  return new Promise((resolve) => {
    let buf = "";
    req.on("data", (c) => { buf += c; if (buf.length > 64 * 1024) req.destroy(); });
    req.on("end", () => { try { resolve(JSON.parse(buf || "{}")); } catch (e) { resolve({}); } });
  });
}
async function handle(req, res) {
  if (req.headers.origin) return json(res, 403, { ok: false, why: "no cross-origin" });
  const url = (req.url || "").split("?")[0];
  if (url === "/ping") return json(res, 200, { ok: true, what: "ticketdesk-terminal-bridge", version: "0.1.0", routes: ["terminals", "show", "type"] });
  if (url === "/terminals") {
    const out = [];
    for (const t of vscode.window.terminals) {
      let pid = null; try { pid = await t.processId; } catch (e) {}
      out.push({ pid: pid === undefined ? null : pid, name: t.name, active: t === vscode.window.activeTerminal });
    }
    return json(res, 200, { ok: true, terminals: out });
  }
  if (req.method !== "POST") return json(res, 405, { ok: false, why: "POST only" });
  const body = await readBody(req);
  const pid = Number(body.pid);
  if (!pid) return json(res, 400, { ok: false, why: "need pid" });
  const t = await findByPid(pid);
  if (!t) return json(res, 200, { ok: false, why: "no terminal with pid " + pid + " in this window" });
  if (url === "/show") { t.show(false); return json(res, 200, { ok: true, shown: t.name }); }
  if (url === "/type") {
    const text = String(body.text || "");
    if (!text || /[\r\n]/.test(text) || text.length > 4000) return json(res, 400, { ok: false, why: "one line, 1-4000 chars" });
    if (!text.startsWith("【")) return json(res, 400, { ok: false, why: "text must start with 【 so it never looks like a shell command" });
    t.sendText(text, true);
    return json(res, 200, { ok: true, typed: t.name });
  }
  return json(res, 404, { ok: false, why: "no route" });
}
function listen(port, last) {
  const s = http.createServer((req, res) => handle(req, res).catch((e) => json(res, 500, { ok: false, why: String(e) })));
  s.on("error", () => { if (port < last) listen(port + 1, last); });
  s.listen(port, "127.0.0.1", () => { server = s; });
}
function activate() { listen(8721, 8728); }
function deactivate() { if (server) server.close(); }
module.exports = { activate, deactivate };
