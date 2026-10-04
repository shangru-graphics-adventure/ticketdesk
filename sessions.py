# -*- coding: utf-8 -*-
"""ticketdesk -- live Claude Code sessions panel.

What is each running session working on, which tickets does that touch, and which ones are waiting for you?

  * live sessions      <claude dir>/sessions/<pid>.json (written by Claude Code; process must still be alive)
  * what it works on   the session transcript <claude dir>/projects/*/<session id>.jsonl, read from the end:
                         prompts           type=user human text
                         mid-turn prompts  attachment/queued_command (messages typed while the agent was busy)
                         turn boundary     system/turn_duration
                         recap             system/away_summary (Claude Code's own "while you were away" recap)
  * ticket refs        "T012" in a prompt, or a bare "12"/"012" that matches an existing ticket and is not
                       followed by a unit (marked as a guess), plus every ticket the session logged to during the turn
  * reply / focus      through the VS Code bridge in integrations/vscode (optional): POST /type and /show on 127.0.0.1:8721-8728

Everything is read-only except reply / deliver (type into the session's terminal, or queue it for the inbox hook) and focus. Standard library only.
Set CLAUDE_DIR to point somewhere other than ~/.claude.
"""
import ctypes, glob, json, os, re, subprocess, sys, time, urllib.request

CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
TAIL = 2_500_000
BRIDGE_PORTS = range(8721, 8729)
_cache = {}

TRE = re.compile(r"(?<![A-Za-z0-9])[Tt](\d{1,5})(?!\d)")
BARE = re.compile(r"(?<![A-Za-z0-9.\-+$#/:])(\d{1,4})(?!\d|\.\d|\s*(?:%|ms|s\b|min|h\b|k|K|M|x|px|GB|MB|kb|KB|个|次|分|秒|天|行|条|张|倍|年|月|号|点))")
SKIP_PREFIX = ("<local-command", "<system-reminder>", "<task-notification>", "Caveat:", "[Request interrupted")


# ---------------------------------------------------------------- processes
def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def parent_pid(pid):
    """Parent of a process = the shell that owns the terminal tab (what the VS Code bridge knows as Terminal.processId)."""
    pid = int(pid)
    if sys.platform == "win32":
        class PE(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_wchar * 260)]
        k = ctypes.windll.kernel32
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snap = k.CreateToolhelp32Snapshot(2, 0)
        e = PE(); e.dwSize = ctypes.sizeof(PE)
        try:
            ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
            while ok:
                if e.th32ProcessID == pid:
                    return e.th32ParentProcessID
                ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
        finally:
            k.CloseHandle(ctypes.c_void_p(snap))
        return None
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except OSError:
        try:
            return int(subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip())
        except Exception:
            return None


def live_sessions():
    out = {}
    for f in glob.glob(os.path.join(CLAUDE_DIR, "sessions", "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get("sessionId") and d.get("pid") and d.get("kind", "interactive") == "interactive" and pid_alive(d["pid"]):
            out[d["sessionId"]] = d
    return out


# ---------------------------------------------------------------- transcript digest
def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return ""
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _clean_prompt(t):
    t = (t or "").strip()
    if not t or t.startswith(SKIP_PREFIX):
        return ""
    m = re.search(r"<command-name>/?([^<]+)</command-name>", t)
    if m:
        g = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        return ("/" + m.group(1).strip() + " " + (g.group(1) if g else "").strip()).strip()
    return "" if t.startswith("<") else t


def _ep(iso):
    if not iso:
        return 0
    try:
        import datetime
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def refs_in(text, known):
    """-> (explicit T ids, guessed bare numbers). Only ids that exist count."""
    sure, guess = [], []
    for m in TRE.finditer(text or ""):
        tid = "T%03d" % int(m.group(1))
        if tid in known and tid not in sure:
            sure.append(tid)
    for m in BARE.finditer(text or ""):
        tid = "T%03d" % int(m.group(1))
        if tid in known and tid not in sure and tid not in guess:
            guess.append(tid)
    return sure, guess


def short(t, n=80):
    t = re.sub(r"\s+", " ", t or "").strip()
    first = re.split(r"(?<=[.?!。？！；;])\s+|\n", t)[0] if t else ""
    s = first if 6 <= len(first) <= n else t
    return s if len(s) <= n else s[:n - 1] + "…"


def points_of(text, limit=9):
    """Key points of a reply: the opening paragraph plus headings / list items / bold lines; code and tables dropped."""
    text = re.sub(r"```.*?```", "", text or "", flags=re.S)
    out, para = [], ""
    for l in text.splitlines():
        s = l.strip()
        if not s:
            if para and not out:
                out.append(para)
            para = ""
            continue
        if s.startswith("|") or set(s) <= set("-=|: "):
            continue
        if not out and not para and not re.match(r"^(#|[-*•]|\d+[.)])", s):
            para = s
            continue
        if re.match(r"^(#{1,4} |[-*•] |\d+[.)] |\*\*)", s):
            out.append(s)
        elif para:
            para += " " + s
    if para and not out:
        out.append(para)
    return [re.sub(r"^(#{1,4} |[-*•] |\d+[.)] )", "", x).replace("**", "") for x in out][:limit]


def transcript(sid):
    hits = glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", sid + ".jsonl"))
    return hits[0] if hits else None


def digest(sid, known=frozenset(), logs=()):
    """-> {tasks, turn_open, points, points_src, last_reply, last_end, title}. logs = [(epoch, ticket id)] this session wrote."""
    f = transcript(sid)
    if not f:
        return None
    st = os.stat(f)
    key = (st.st_mtime, st.st_size, len(known), len(logs))
    c = _cache.get(sid)
    if c and c[0] == key:
        return c[1]
    with open(f, "rb") as fh:
        fh.seek(max(0, st.st_size - TAIL))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    if st.st_size > TAIL:
        lines = lines[1:]

    def new_turn(ts):
        return dict(prompts=[], end=None, texts=[], start=ts)
    turns = [new_turn("")]
    away, title = None, ""
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("isSidechain"):
            continue
        ty, ts, cur = r.get("type"), r.get("timestamp") or "", turns[-1]
        if ty == "user" and not r.get("isMeta"):
            t = _clean_prompt(_text((r.get("message") or {}).get("content")))
            if t:
                if cur["end"]:
                    cur = new_turn(ts); turns.append(cur)
                cur["prompts"].append(dict(ts=ts, text=t, via="mid-turn" if cur["prompts"] else "prompt"))
        elif ty == "attachment":
            a = r.get("attachment") or {}
            if a.get("type") == "queued_command" and a.get("commandMode", "prompt") == "prompt":
                t = _clean_prompt(a.get("prompt") or "")
                if t:
                    if cur["end"]:
                        cur = new_turn(ts); turns.append(cur)
                    cur["prompts"].append(dict(ts=a.get("timestamp") or ts, text=t, via="mid-turn"))
        elif ty == "assistant":
            c2 = (r.get("message") or {}).get("content")
            if isinstance(c2, list):
                txt = "\n".join(x.get("text", "") for x in c2 if isinstance(x, dict) and x.get("type") == "text").strip()
                if any(isinstance(x, dict) and x.get("type") == "tool_use" for x in c2):
                    cur["texts"] = [txt] if txt else []
                elif txt:
                    cur["texts"].append(txt)
        elif ty == "system" and r.get("subtype") == "turn_duration":
            cur["end"] = ts
        elif ty == "system" and r.get("subtype") == "away_summary":
            away = dict(ts=ts, text=re.sub(r"\s*\(disable recaps in /config\)\s*$", "", r.get("content") or ""))
        elif ty == "ai-title":
            title = r.get("aiTitle") or title
    turns = [t for t in turns if t["prompts"]]
    now = time.time()
    keep = [t for t in turns if not t["end"] or now - _ep(t["end"]) < 12 * 3600][-4:] or turns[-1:]
    tasks = []
    for t in keep:
        state = "running" if not t["end"] else ("answered" if t is turns[-1] else "done")
        t0, t1 = _ep(t["prompts"][0]["ts"]), (_ep(t["end"]) if t["end"] else now + 1)
        logged = []
        for e, tid in logs:
            if t0 - 5 <= e <= t1 + 60 and tid not in logged:
                logged.append(tid)
        for p in t["prompts"]:
            sure, guess = refs_in(p["text"], known)
            via = p["via"]
            if len(p["text"].strip()) <= 12 and not sure and not guess:
                via = "reply"                          # "ok", "go ahead": an answer to the last turn, not a new task
            tasks.append(dict(ts=p["ts"], via=via, text=short(p["text"]), full=p["text"][:1500], refs=sure, guess=guess, state=state))
        have = {x for k in tasks if k["state"] == state for x in k["refs"] + k["guess"]}
        extra = [x for x in logged if x not in have]
        if extra:
            tasks.append(dict(ts=t["prompts"][-1]["ts"], via="logged", text="", full="", refs=extra, guess=[], state=state))
    last = turns[-1] if turns else None
    reply = "\n\n".join(last["texts"]) if last and last["end"] else ""
    if away and last and last["end"] and away["ts"] >= last["end"]:
        pts, src = [away["text"]], "recap"
    else:
        pts, src = points_of(reply), "reply"
    res = dict(tasks=tasks, turn_open=bool(last and not last["end"]), points=pts, points_src=src if pts else "",
               last_reply=reply[-6000:], last_end=_ep(last["end"]) if last and last["end"] else 0, title=title)
    _cache[sid] = (key, res)
    return res


def build(db):
    """-> {rows: [...], bridge: bool}. One row per live interactive Claude Code session, waiting-for-you first."""
    known = frozenset(t["id"] for t in db.get("tickets", []))
    logs = {}
    for t in db.get("tickets", []):
        for e in t.get("log") or []:
            if e.get("session"):
                logs.setdefault(e["session"], []).append((_ep(e.get("ts")), t["id"]))
    rows = []
    for sid, s in live_sessions().items():
        try:
            w = digest(sid, known, logs.get(sid, []))
        except Exception as e:                           # a broken transcript must not take the panel down
            w = dict(error=repr(e))
        rows.append(dict(sid=sid, name=s.get("name") or "", status=s.get("status") or "", cwd=s.get("cwd") or "",
                         started=(s.get("startedAt") or 0) / 1000, pid=s.get("pid"), work=w))
    order = {"idle": 0, "waiting": 0, "shell": 0, "busy": 1}
    rows.sort(key=lambda r: (order.get(r["status"], 1), -((r["work"] or {}).get("last_end") or r["started"])))
    return dict(rows=rows, claude_dir=CLAUDE_DIR)


# ---------------------------------------------------------------- VS Code bridge (optional)
def _bridge(path, body):
    import socket
    last = "no VS Code bridge running (see integrations/vscode)"
    for port in BRIDGE_PORTS:
        s = socket.socket(); s.settimeout(0.15)
        try:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue
        finally:
            s.close()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = repr(e); continue
        if d.get("ok"):
            return d
        last = d.get("why") or last
    return {"ok": False, "why": last}


def _shell_of(sid):
    s = live_sessions().get(sid)
    if not s:
        raise ValueError("session is not running")
    sp = parent_pid(s["pid"])
    if not sp:
        raise ValueError("cannot find the terminal of this session")
    return s, sp


def focus(sid):
    _, sp = _shell_of(sid)
    return _bridge("/show", {"pid": sp})


def reply(sid, text):
    """Reply to a live session. Idle -> typed into its terminal (VS Code bridge) and Enter pressed.
    Running, or showing a permission prompt -> queued in the inbox; the inbox hook hands it over after the
    session's next tool call (or holds the turn open at Stop), so nothing is typed into a running turn."""
    if not str(text or "").strip():
        raise ValueError("empty reply")
    _shell_of(sid)                                    # must be a live session
    return deliver(sid, text, "you")


# ---------------------------------------------------------------- inbox (messages for sessions that are busy or closed)
# <data>/inbox/<sid>.jsonl, append-only. integrations/claude-code/inbox_hook.py claims the file with a rename
# (so a message is never delivered twice and never races the server's append), injects the messages and moves
# them to <sid>.done.jsonl. The server points INBOX at <data>/inbox at start-up.
INBOX = os.path.join(os.path.expanduser("~"), ".ticketdesk", "inbox")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def queue(sid, text, frm):
    os.makedirs(INBOX, exist_ok=True)
    m = dict(id="m%d" % time.time_ns(), ts=_now(), frm=frm, text=text)
    with open(os.path.join(INBOX, sid + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return m


def _read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def messages(sid, n=30):
    """Delivered (newest n) + still queued messages for one session."""
    done = _read_jsonl(os.path.join(INBOX, sid + ".done.jsonl"))[-n:]
    return done + [dict(m, mode="queued") for m in _read_jsonl(os.path.join(INBOX, sid + ".jsonl"))]


def unqueue(sid, mid):
    """Take back a queued message that has not been delivered yet. Same claim-by-rename as the hook:
    if the file is gone, the hook already took it and it cannot be recalled."""
    f = os.path.join(INBOX, sid + ".jsonl")
    claim = os.path.join(INBOX, "%s.unsend%d" % (sid, time.time_ns()))
    try:
        os.replace(f, claim)
    except OSError:
        return {"ok": False, "why": "already delivered"}
    keep, hit = [], False
    for m in _read_jsonl(claim):
        if m.get("id") == mid and not hit:
            hit = True
            continue
        keep.append(json.dumps(m, ensure_ascii=False) + "\n")
    if keep:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("".join(keep))
    os.remove(claim)
    return {"ok": hit, "why": "" if hit else "not in the queue; probably delivered already"}


def deliver(sid, text, frm="you"):
    """Get text to a session one way or another. -> {ok, mode: typed|queued, why}.
    typed:  the session is live and idle and a VS Code bridge owns its terminal (same as you typing it).
    queued: everything else -- running, permission prompt, no bridge, or not open at all (delivered when it next runs)."""
    text = re.sub(r"\s*\n\s*", " ", str(text or "").strip())
    if not text:
        raise ValueError("empty message")
    if len(text) > 3900:
        raise ValueError("message too long (> 3900 characters)")
    s = live_sessions().get(sid)
    if s and s.get("status") == "idle":
        sp = parent_pid(s["pid"])
        r = _bridge("/type", {"pid": sp, "text": "【desk · %s】%s" % (frm, text)}) if sp else {"ok": False, "why": "no terminal"}
        if r.get("ok"):
            os.makedirs(INBOX, exist_ok=True)
            with open(os.path.join(INBOX, sid + ".done.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(ts=_now(), frm=frm, text=text, mode="typed"), ensure_ascii=False) + "\n")
            return {"ok": True, "mode": "typed", "why": ""}
        why = "idle but could not type (%s)" % r.get("why")
    else:
        why = "session is %s" % ((s or {}).get("status") or "not open")
    m = queue(sid, text, frm)
    return {"ok": True, "mode": "queued", "id": m["id"], "why": why + "; queued, the inbox hook delivers it when the session next runs"}
