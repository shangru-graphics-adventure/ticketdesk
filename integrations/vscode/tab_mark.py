# -*- coding: utf-8 -*-
"""Mark Claude Code terminal tabs that are waiting for you with "▶ " -- without moving or switching tabs.

Claude Code hook (see README.md in this folder):
    Stop / Notification(permission)  -> title becomes "▶ <label>"   (+ a short chime, unless TAB_MARK_SILENT=1)
    UserPromptSubmit                 -> title becomes "<label>"

Why the console title and not VS Code's rename: VS Code's renameWithArg only acts on the *active* terminal, so renaming
a background tab means switching to it and back (a visible flicker), and a tab renamed through the API ignores any
title the process sets afterwards. Reordering tabs is worse: commands can only move a tab by sending it to the editor
area and back, which flashes the whole window. The console title needs no VS Code call at all:
    Windows      AttachConsole(claude pid) + SetConsoleTitleW   (ConPTY turns it into an OSC 0 sequence)
    macOS/Linux  write ESC ] 0 ; title BEL to the claude process's tty
VS Code shows that as the tab name, as long as the tab was never renamed by hand / through the API.
Set CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 so Claude Code does not overwrite it.

    python tab_mark.py Stop|Notification|UserPromptSubmit < hook payload     (the hook)
    python tab_mark.py label <session id> "<label>"                          (name a tab; keeps the ▶ state)

Labels live in ~/.claude/tab_labels.json; default label = Claude Code's own title for the session.
Never raises; always exits 0 when run as a hook."""
import glob, io, json, os, subprocess, sys, time

CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
REG = os.path.join(CLAUDE_DIR, "tab_labels.json")
MARK = "▶ "
ACTIVE = {"running", "pending", "in_progress", "started", "queued"}


def session(sid):
    for f in glob.glob(os.path.join(CLAUDE_DIR, "sessions", "*.json")):
        try:
            with io.open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get("sessionId") == sid:
            return d
    return None


def labels():
    try:
        with io.open(REG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_labels(d):
    tmp = REG + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, REG)


def default_label(sid, s):
    title = ""
    for f in glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", sid + ".jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 2_000_000))
                for line in fh.read().decode("utf-8", "replace").splitlines():
                    if '"ai-title"' in line:
                        try:
                            title = json.loads(line).get("aiTitle") or title
                        except ValueError:
                            pass
        except OSError:
            pass
    return "%s [%s]" % ((title or "Claude")[:40], s.get("name") or sid[:8])


# ---------------------------------------------------------------- setting the title
def _win_title(pid, text):
    """Runs in a child process with no console of its own (CREATE_NO_WINDOW), so it can attach to claude's."""
    code = ("import ctypes,sys;k=ctypes.windll.kernel32;k.FreeConsole();"
            "ok=k.AttachConsole(int(sys.argv[1])) and k.SetConsoleTitleW(sys.argv[2]);k.FreeConsole();sys.exit(0 if ok else 1)")
    r = subprocess.run([sys.executable, "-c", code, str(pid), text], timeout=10, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0


def _tty_of(pid):
    try:
        p = os.readlink("/proc/%d/fd/0" % pid)                        # Linux
        if p.startswith("/dev/"):
            return p
    except OSError:
        pass
    try:
        t = subprocess.check_output(["ps", "-o", "tty=", "-p", str(pid)], text=True).strip()   # macOS
        return "/dev/" + t if t and t not in ("?", "??") else None
    except Exception:
        return None


def set_title(pid, text):
    if sys.platform == "win32":
        return _win_title(pid, text)
    tty = _tty_of(int(pid))
    if not tty:
        return False
    with open(tty, "w") as f:
        f.write("\033]0;%s\007" % text.replace("\007", ""))
    return True


def apply(sid, waiting=None, label=None):
    s = session(sid)
    if not s:
        return False
    reg = labels()
    if label:
        reg[sid] = label.replace(MARK, "").strip()[:80]
        save_labels(reg)
    lab = reg.get(sid)
    if not lab:
        lab = reg[sid] = default_label(sid, s)
        save_labels(reg)
    if waiting is None:
        waiting = s.get("status") in ("idle", "waiting")
    return set_title(s["pid"], (MARK if waiting else "") + lab)


# ---------------------------------------------------------------- hook
def chime():
    if os.environ.get("TAB_MARK_SILENT") == "1":
        return
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif sys.platform == "darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            sys.stderr.write("\a")
    except Exception:
        pass


def busy_in_background(p):
    bt = p.get("background_tasks") or []
    items = bt.values() if isinstance(bt, dict) else bt if isinstance(bt, list) else [bt]
    return any(not isinstance(x, dict) or str(x.get("status") or "").lower() in ACTIVE | {""} for x in items)


def decide(event, p, s):
    """-> waiting? (True = mark, False = unmark, None = leave alone)"""
    if event == "UserPromptSubmit":
        return False
    if not s or s.get("kind", "interactive") != "interactive":
        return None                                  # headless `claude -p` and the like
    if event == "Stop":
        if busy_in_background(p) or p.get("stop_hook_active"):
            return None                              # it will wake up again by itself: not your turn yet
        return True
    if event == "Notification":
        m = str(p.get("message") or "").lower()
        return True if "permission" in m else None
    return None


def hook(event):
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        sid = p.get("session_id") or ""
        w = decide(event, p, session(sid))
        if w is None:
            return
        apply(sid, w)
        if w:
            chime()
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "label":
        sys.exit(0 if apply(sys.argv[2], None, sys.argv[3]) else 1)
    hook(sys.argv[1] if len(sys.argv) > 1 else "Stop")
    sys.exit(0)
