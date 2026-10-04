# -*- coding: utf-8 -*-
"""ticketdesk server -- a local ticket desk for humans and AI agents.

Three record types live in one JSON file:

  tickets    T001  nested question/answer cards with an append-only work log
  proposals  P001  actions that need a human's approval before anyone runs them
  knowledge  K001  reusable facts that tickets cite; can be retracted / superseded

Every write is atomic, the previous file is rotated into backups/, and the last
60 writes can be undone. Pure standard library, Python 3.8+.

  python server.py [--data DIR] [--port 8750] [--allow-run] [--scripts-dir DIR]

Security model (local tool, single user):
  * binds 127.0.0.1 only; requests whose Host header is not localhost are refused
    (DNS-rebinding guard)
  * every POST must carry the header  X-Desk-Client: 1  -- a cross-site page
    cannot add custom headers without a CORS preflight, which this server never
    approves (CSRF guard)
  * approving a proposal never executes anything unless the server was started
    with --allow-run AND the proposal's approve_cmd is a script file inside
    --scripts-dir
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = 1

STATUSES = ("open", "partial", "answered")
PRIORITIES = ("P0", "P1", "P2", "")
P_STATUSES = ("pending", "running", "done", "rejected")
K_STATUSES = ("current", "retracted")
T_FIELDS = ("title", "question", "answer_title", "answer", "status", "priority", "tags", "links", "source")
P_FIELDS = ("title", "summary", "body", "status", "priority", "approve_cmd", "tickets", "tags", "links")
K_FIELDS = ("title", "body", "tags", "links", "source")
REF_RE = re.compile(r"\b([TPK])(\d{3,})\b")
SCRIPT_EXT = (".py", ".sh", ".cmd", ".bat", ".ps1")
MAX_LINKS, MAX_TAGS, MAX_UNDO, MAX_BACKUPS = 200, 30, 60, 200


class Store:
    """JSON file store with atomic writes, rotating backups and an undo stack."""

    def __init__(self, data_dir):
        self.dir = os.path.abspath(data_dir)
        self.path = os.path.join(self.dir, "desk.json")
        self.bak = os.path.join(self.dir, "backups")
        self.runs = os.path.join(self.dir, "runs")
        self.lock = threading.RLock()
        self.undo = []
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.path):
            self._write(empty_db())

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            d = json.load(f)
        for k in ("tickets", "proposals", "knowledge"):
            d.setdefault(k, [])
        d.setdefault("next", {})
        for k in "TPK":
            d["next"].setdefault(k, 1)
        return d

    def _write(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def save(self, d):
        os.makedirs(self.bak, exist_ok=True)
        if os.path.exists(self.path):
            name = time.strftime("desk_%Y%m%d_%H%M%S_") + "%06d.json" % (time.time_ns() // 1000 % 10**6)
            shutil.copy(self.path, os.path.join(self.bak, name))
            olds = sorted(os.listdir(self.bak))
            for o in olds[:-MAX_BACKUPS]:
                os.remove(os.path.join(self.bak, o))
        self._write(d)

    def mutate(self, fn):
        with self.lock:
            d = self.load()
            snap = json.dumps(d, ensure_ascii=False)
            res = fn(d)
            self.save(d)
            self.undo.append(snap)
            del self.undo[:-MAX_UNDO]
            return res

    def undo_last(self):
        with self.lock:
            if not self.undo:
                raise ValueError("nothing to undo")
            self.save(json.loads(self.undo.pop()))
            return len(self.undo)


def empty_db():
    return {"version": VERSION, "next": {"T": 1, "P": 1, "K": 1}, "tickets": [], "proposals": [], "knowledge": []}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- validation

def split_list(v):
    if isinstance(v, str):
        v = re.split(r"[,，\s]+", v)
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def clean_links(v):
    """[{label, url}] or text with one 'label|url' (or bare url) per line."""
    if isinstance(v, str):
        out = []
        for line in v.splitlines():
            line = line.strip()
            if not line:
                continue
            lab, sep, url = line.partition("|")
            out.append({"label": lab.strip(), "url": url.strip()} if sep else {"label": line, "url": line})
        v = out
    v = [{"label": str(x.get("label") or x.get("url") or ""), "url": str(x.get("url") or "")} for x in (v or []) if isinstance(x, dict)]
    if len(v) > MAX_LINKS:  # refuse loudly instead of silently truncating
        raise ValueError("more than %d links (%d); split into child tickets" % (MAX_LINKS, len(v)))
    return v


def clean_fields(body, allowed, partial, statuses, defaults):
    out = {}
    for k in allowed:
        if k not in body:
            continue
        v = body[k]
        if k == "tags":
            v = split_list(v)
            if len(v) > MAX_TAGS:
                raise ValueError("more than %d tags (%d)" % (MAX_TAGS, len(v)))
        elif k == "tickets":
            v = [x.upper() for x in split_list(v)]
        elif k == "links":
            v = clean_links(v)
        elif k == "status":
            if v not in statuses:
                raise ValueError("status must be one of %s" % (statuses,))
        elif k == "priority":
            v = str(v or "").upper().strip()
            if v not in PRIORITIES:
                raise ValueError("priority must be one of P0, P1, P2 or empty")
        else:
            v = "" if v is None else str(v)
        out[k] = v
    if not partial:
        if not str(out.get("title", "")).strip():
            raise ValueError("title must not be empty")
        for k, dv in defaults.items():
            out.setdefault(k, dv() if callable(dv) else dv)
    elif "title" in out and not out["title"].strip():
        raise ValueError("title must not be empty")
    return out


T_DEFAULTS = {"status": "open", "priority": "", "question": "", "answer_title": "", "answer": "", "source": "", "tags": list, "links": list}
P_DEFAULTS = {"status": "pending", "priority": "", "summary": "", "body": "", "approve_cmd": "", "tickets": list, "tags": list, "links": list}
K_DEFAULTS = {"body": "", "source": "", "tags": list, "links": list}


def find(d, rid):
    coll = {"T": "tickets", "P": "proposals", "K": "knowledge"}.get(rid[:1])
    for x in d.get(coll or "tickets", []):
        if x["id"] == rid:
            return x
    raise KeyError("no such id: %s" % rid)


def new_id(d, kind):
    n = d["next"][kind]
    d["next"][kind] = n + 1
    return "%s%03d" % (kind, n)


def log_entry(b, text=None, name=None):
    text = (text if text is not None else str(b.get("text") or "")).strip()
    if not text:
        raise ValueError("log text must not be empty")
    return {"ts": now(), "session": str(b.get("session") or "").strip(),
            "name": (name if name is not None else str(b.get("name") or "")).strip(), "text": text}


def check_ticket_refs(d, ids):
    have = {t["id"] for t in d["tickets"]}
    bad = [x for x in ids if x not in have]
    if bad:
        raise ValueError("linked tickets do not exist: " + ", ".join(bad))


def ancestors(d, tid):
    by = {t["id"]: t for t in d["tickets"]}
    out = []
    while tid and tid in by and tid not in out:
        out.append(tid)
        tid = by[tid]["parent"]
    return out


def reorder(d, parent):
    sib = sorted([t for t in d["tickets"] if t["parent"] == parent], key=lambda t: t["order"])
    for i, t in enumerate(sib):
        t["order"] = i + 1


# ---------------------------------------------------------------- consistency check

def text_of(x):
    parts = [x.get(k) or "" for k in ("title", "question", "answer_title", "answer", "summary", "body")]
    parts += [e.get("text", "") for e in x.get("log") or []]
    return "\n".join(parts)


def refs_in(x):
    return sorted({m.group(0) for m in REF_RE.finditer(text_of(x))} - {x["id"]})


def check(d):
    """Problems a reader should know about before trusting a record."""
    ids = {x["id"] for c in ("tickets", "proposals", "knowledge") for x in d[c]}
    kb = {k["id"]: k for k in d["knowledge"]}
    issues = []
    for c in ("tickets", "proposals", "knowledge"):
        for x in d[c]:
            for r in refs_in(x):
                if r not in ids:
                    issues.append({"kind": "dangling_ref", "id": x["id"], "ref": r, "msg": "%s mentions %s, which does not exist" % (x["id"], r)})
                elif r in kb and kb[r]["status"] == "retracted" and c != "knowledge":
                    sup = kb[r].get("superseded_by") or ""
                    issues.append({"kind": "cites_retracted", "id": x["id"], "ref": r, "superseded_by": sup,
                                   "msg": "%s cites retracted %s%s" % (x["id"], r, (" (use %s instead)" % sup) if sup else "")})
    for p in d["proposals"]:
        for t in p.get("tickets") or []:
            if t not in ids:
                issues.append({"kind": "dangling_ref", "id": p["id"], "ref": t, "msg": "%s is linked to missing ticket %s" % (p["id"], t)})
    return issues


def cited_by(d, kid):
    return [x["id"] for c in ("tickets", "proposals") for x in d[c] if kid in refs_in(x)]


# ---------------------------------------------------------------- approval scripts

class Runner:
    def __init__(self, store, allow, scripts_dir):
        self.store, self.allow = store, allow
        self.dir = os.path.realpath(scripts_dir) if scripts_dir else None

    def script_of(self, cmd):
        """approve_cmd -> absolute script path if it may be executed, else None."""
        if not (self.allow and self.dir):
            return None
        c = str(cmd or "").strip().strip('"').strip("'")
        if not c:
            return None
        p = os.path.realpath(c if os.path.isabs(c) else os.path.join(self.dir, c))
        if not p.startswith(self.dir + os.sep) or not os.path.isfile(p) or os.path.splitext(p)[1].lower() not in SCRIPT_EXT:
            return None
        return p

    def argv(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".py":
            return [sys.executable, path]
        if ext == ".ps1":
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        if ext in (".cmd", ".bat"):
            return ["cmd.exe", "/d", "/c", path]
        return ["sh", path]

    def run(self, pid, path, logf):
        """Run in the background with stdin closed (prompts return immediately); write the result back."""
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        rc, err = None, ""
        try:
            with open(logf, "wb") as lf:
                r = subprocess.run(self.argv(path), cwd=os.path.dirname(path), stdin=subprocess.DEVNULL, stdout=lf,
                                   stderr=subprocess.STDOUT, timeout=3600, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rc = r.returncode
        except Exception as e:  # noqa: BLE001 - report every failure on the proposal
            err = "%s: %s" % (type(e).__name__, e)
        try:
            with open(logf, "rb") as f:
                txt = f.read().decode("utf-8", errors="replace")
        except OSError:
            txt = ""
        tail = "\n".join([x for x in txt.splitlines() if x.strip()][-30:])
        ok = rc == 0 and not err

        def f(d):
            x = find(d, pid)
            x.setdefault("exec", {}).update(state="ok" if ok else "failed", rc=rc, ended=now(), tail=tail, error=err)
            x.setdefault("log", []).append({"ts": now(), "session": "", "name": "desk runner",
                                            "text": ("run succeeded" if ok else "run FAILED rc=%s %s" % (rc, err)) + " (log %s)\n%s" % (logf, tail[-1500:])})
            x["updated"] = now()
        self.store.mutate(f)


# ---------------------------------------------------------------- HTTP

def make_handler(store, runner, port):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}

    class H(BaseHTTPRequestHandler):
        server_version = "ticketdesk/1"

        def log_message(self, *a):
            pass

        def send(self, code, obj, ctype="application/json; charset=utf-8"):
            b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def host_ok(self):
            return (self.headers.get("Host") or "").lower() in allowed_hosts

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 5 * 2**20:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

        # ---- GET
        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "bad Host header"})
            p = urlsplit(self.path).path
            if p in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if p in ("/sessions.js", "/md.js"):
                with open(os.path.join(HERE, p[1:]), "rb") as f:
                    return self.send(200, f.read(), "text/javascript; charset=utf-8")
            if p == "/api/health":
                return self.send(200, {"ok": True, "version": VERSION, "allow_run": bool(runner.allow and runner.dir)})
            with store.lock:
                d = store.load()
            if p == "/api/state":
                return self.send(200, d)
            if p == "/api/check":
                return self.send(200, check(d))
            if p == "/api/sessions":                       # live Claude Code sessions (sessions.py)
                import sessions
                return self.send(200, sessions.build(d))
            m = re.fullmatch(r"/api/(tickets|proposals|knowledge)/([TPK]\d+)", p)
            if m:
                try:
                    x = find(d, m.group(2))
                except KeyError as e:
                    return self.send(404, {"error": str(e).strip("'")})
                if x["id"][0] == "K":
                    x = dict(x, cited_by=cited_by(d, x["id"]))
                return self.send(200, x)
            self.send(404, {"error": "not found"})

        # ---- POST
        def do_POST(self):
            if not self.host_ok():
                return self.send(403, {"error": "bad Host header"})
            if self.headers.get("X-Desk-Client") != "1":
                return self.send(403, {"error": "missing X-Desk-Client header"})
            p = urlsplit(self.path).path
            try:
                b = self.body()
                if not isinstance(b, dict):
                    raise ValueError("body must be a JSON object")
                if p == "/api/undo":
                    return self.send(200, {"ok": True, "left": store.undo_last()})
                m = re.fullmatch(r"/api/sessions/([0-9a-f-]{36})/(reply|focus)", p)
                if m:
                    import sessions
                    sid, act = m.groups()
                    r = sessions.reply(sid, str(b.get("text") or "")) if act == "reply" else sessions.focus(sid)
                    return self.send(200 if r.get("ok") else 409, r if r.get("ok") else {"error": r.get("why") or "failed"})
                m = re.fullmatch(r"/api/(tickets|proposals|knowledge)(?:/([TPK]\d+)(?:/(\w+))?)?", p)
                if not m:
                    return self.send(404, {"error": "not found"})
                coll, rid, act = m.groups()
                handler = {"tickets": self.ticket, "proposals": self.proposal, "knowledge": self.knowledge}[coll]
                after = []
                res = store.mutate(lambda d: handler(d, b, rid, act, after))
                for job in after:
                    threading.Thread(target=runner.run, args=job, daemon=True).start()
                return self.send(200, res)
            except KeyError as e:
                self.send(404, {"error": str(e).strip("'")})
            except (ValueError, TypeError) as e:
                self.send(400, {"error": str(e)})

        def ticket(self, d, b, tid, act, after):
            if tid is None:
                t = clean_fields(b, T_FIELDS, False, STATUSES, T_DEFAULTS)
                par = b.get("parent") or None
                if par:
                    find(d, par)
                t.update(id=new_id(d, "T"), parent=par, log=[], created=now(), updated=now(),
                         order=max([x["order"] for x in d["tickets"] if x["parent"] == par] or [0]) + 1)
                d["tickets"].append(t)
                return t
            t = find(d, tid)
            if act is None:
                t.update(clean_fields(b, T_FIELDS, True, STATUSES, T_DEFAULTS))
            elif act == "log":
                e = log_entry(b)
                t.setdefault("log", []).append(e)
                t["updated"] = now()
                return e
            elif act == "move":
                par = b.get("parent") or None
                if par:
                    find(d, par)
                    if tid in ancestors(d, par):
                        raise ValueError("cannot move a ticket under itself or its descendants")
                old = t["parent"]
                t["parent"] = par
                sib = sorted([x for x in d["tickets"] if x["parent"] == par and x is not t], key=lambda x: x["order"])
                idx = next((i for i, x in enumerate(sib) if x["id"] == b.get("before")), len(sib))
                sib.insert(idx, t)
                for i, x in enumerate(sib):
                    x["order"] = i + 1
                reorder(d, old)
            elif act == "delete":
                for c in d["tickets"]:  # children move up one level
                    if c["parent"] == tid:
                        c["parent"] = t["parent"]
                        c["order"] += 10000
                d["tickets"].remove(t)
                reorder(d, t["parent"])
                return {"ok": True}
            else:
                raise KeyError("unknown action: %s" % act)
            t["updated"] = now()
            return t

        def proposal(self, d, b, pid, act, after):
            def by(default):
                return str(b.get("by") or default).strip()
            if pid is None:
                x = clean_fields(b, P_FIELDS, False, P_STATUSES, P_DEFAULTS)
                check_ticket_refs(d, x["tickets"])
                if x["status"] in ("done", "rejected"):
                    raise ValueError("a new proposal starts as pending or running")
                x.update(id=new_id(d, "P"), log=[], created=now(), updated=now())
                if x["status"] == "running":
                    x.update(approved_at=now(), approved_by=by("created as running"))
                d["proposals"].append(x)
                return x
            x = find(d, pid)
            if act is None:
                c = clean_fields(b, P_FIELDS, True, P_STATUSES, P_DEFAULTS)
                if "status" in c:
                    raise ValueError("change status with /approve, /run, /reject or /done")
                if "tickets" in c:
                    check_ticket_refs(d, c["tickets"])
                x.update(c)
            elif act == "log":
                x.setdefault("log", []).append(log_entry(b))
            elif act in ("approve", "run"):
                if x["status"] != "pending":
                    raise ValueError("%s is %s, only pending proposals can be approved" % (pid, x["status"]))
                x.update(status="running", approved_at=now(), approved_by=by("desk button" if act == "run" else "cli"))
                path = runner.script_of(x.get("approve_cmd")) if act == "run" else None
                if path:
                    logf = os.path.join(store.runs, "%s_%s.log" % (pid, time.strftime("%Y%m%d_%H%M%S")))
                    x["exec"] = {"state": "running", "cmd": path, "started": now(), "log": logf}
                    x.setdefault("log", []).append(log_entry(b, "approved by %s; running %s" % (x["approved_by"], path)))
                    after.append((pid, path, logf))
                else:
                    why = "" if act == "approve" else (" (not executed: server started without --allow-run)" if not runner.allow
                                                       else " (not executed: approve_cmd is not a script inside the scripts dir)")
                    x["exec"] = {"state": "none", "note": "marked approved only; an agent carries it out" + why}
                    x.setdefault("log", []).append(log_entry(b, "approved by %s%s" % (x["approved_by"], why)))
            elif act == "reject":
                if x["status"] not in ("pending", "running"):
                    raise ValueError("%s is already %s" % (pid, x["status"]))
                x["status"] = "rejected"
                x.setdefault("log", []).append(log_entry(b, "rejected" + (": " + str(b.get("reason")).strip() if b.get("reason") else "")))
            elif act == "done":
                summ = str(b.get("summary") or "").strip()
                if not summ:
                    raise ValueError("done needs a summary (result and artifacts)")
                tk = split_list(b.get("tickets")) or list(x.get("tickets") or [])
                if not tk:
                    raise ValueError("no linked ticket to archive into; pass tickets")
                check_ticket_refs(d, tk)
                for tid in tk:
                    t = find(d, tid)
                    t.setdefault("log", []).append(log_entry(b, "[proposal %s done] %s\n%s" % (pid, x["title"], summ),
                                                             name=str(b.get("name") or "proposal archive")))
                    t["updated"] = now()
                x.update(status="done", done_at=now(), archived_to=tk, result=summ)
                x.setdefault("log", []).append(log_entry(b, "done, archived to " + ", ".join(tk) + "\n" + summ))
            else:
                raise KeyError("unknown action: %s" % act)
            x["updated"] = now()
            return x

        def knowledge(self, d, b, kid, act, after):
            if kid is None:
                k = clean_fields(b, K_FIELDS, False, K_STATUSES, K_DEFAULTS)
                k.update(id=new_id(d, "K"), status="current", superseded_by="", log=[], created=now(), updated=now())
                d["knowledge"].append(k)
                return k
            k = find(d, kid)
            if act is None:
                k.update(clean_fields(b, K_FIELDS, True, K_STATUSES, K_DEFAULTS))
            elif act == "log":
                k.setdefault("log", []).append(log_entry(b))
            elif act == "retract":
                reason = str(b.get("reason") or "").strip()
                if not reason:
                    raise ValueError("retract needs a reason")
                sup = str(b.get("superseded_by") or "").strip().upper()
                if sup:
                    if sup == kid:
                        raise ValueError("an entry cannot supersede itself")
                    s = find(d, sup)
                    if s["id"][0] != "K":
                        raise ValueError("superseded_by must be a knowledge id")
                    s.setdefault("supersedes", [])
                    if kid not in s["supersedes"]:
                        s["supersedes"].append(kid)
                k.update(status="retracted", superseded_by=sup, retracted_at=now())
                k.setdefault("log", []).append(log_entry(b, "retracted: %s%s" % (reason, (" -> superseded by " + sup) if sup else "")))
            else:
                raise KeyError("unknown action: %s" % act)
            k["updated"] = now()
            return k

    return H


def main():
    ap = argparse.ArgumentParser(description="ticketdesk server")
    ap.add_argument("--data", default=os.environ.get("DESK_DATA") or os.path.join(os.path.expanduser("~"), ".ticketdesk"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DESK_PORT", "8750")))
    ap.add_argument("--allow-run", action="store_true", default=os.environ.get("DESK_ALLOW_RUN") == "1",
                    help="let the Approve button execute approve_cmd scripts that live inside --scripts-dir")
    ap.add_argument("--scripts-dir", default=os.environ.get("DESK_SCRIPTS_DIR"), help="default: <data>/scripts")
    a = ap.parse_args()
    store = Store(a.data)
    runner = Runner(store, a.allow_run, a.scripts_dir or os.path.join(store.dir, "scripts"))
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(store, runner, a.port))
    print("ticketdesk http://127.0.0.1:%d/  data=%s  allow_run=%s" % (a.port, store.path, a.allow_run), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
