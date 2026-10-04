# -*- coding: utf-8 -*-
"""ticketdesk inbox hook: hand over messages the desk queued for a session that was busy (or closed) when you sent them.

Install it on three Claude Code events (see README "Inbox hook"):

  PostToolUse / UserPromptSubmit -> the messages are added to the context (hookSpecificOutput.additionalContext)
  Stop                           -> {"decision": "block", "reason": ...}: the turn does not end until the messages are handled

Reads <data>/inbox/<session id>.jsonl, where <data> is $DESK_DATA or ~/.ticketdesk. The file is claimed with a rename
first, so a message is never delivered twice and never races the server appending to it; delivered messages move to
<session id>.done.jsonl. When there is nothing queued the hook does one stat() and exits. It swallows every error,
writes nothing to stdout when idle and always exits 0 -- a broken hook must never break the session.
"""
import os
import sys


def inbox_dir():
    return os.path.join(os.environ.get("DESK_DATA") or os.path.join(os.path.expanduser("~"), ".ticketdesk"), "inbox")


def main():
    import json
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return
    sid = str(p.get("session_id") or "")
    ev = str(p.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else ""))
    if len(sid) != 36 or ev not in ("PostToolUse", "UserPromptSubmit", "Stop"):
        return
    box = inbox_dir()
    f = os.path.join(box, sid + ".jsonl")
    if not os.path.exists(f):
        return
    import time
    claim = os.path.join(box, "%s.claim%d" % (sid, time.time_ns()))
    try:
        os.replace(f, claim)
    except OSError:
        return                                   # the server is writing right now; next event delivers
    msgs = []
    with open(claim, encoding="utf-8") as fh:
        for line in fh:
            try:
                msgs.append(json.loads(line))
            except ValueError:
                pass
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(os.path.join(box, sid + ".done.jsonl"), "a", encoding="utf-8") as fh:
        for m in msgs:
            fh.write(json.dumps(dict(m, mode="hook:" + ev, delivered=stamp), ensure_ascii=False) + "\n")
    os.remove(claim)
    if not msgs:
        return
    body = "\n".join("- [%s] %s: %s" % (m.get("ts", ""), m.get("frm", "you"), m.get("text", "")) for m in msgs)
    text = ("[%d message(s) from the user, sent through ticketdesk while you were busy. Treat them as if the user typed "
            "them; handle them first and answer in your next reply.]\n%s" % (len(msgs), body))
    out = {"decision": "block", "reason": text} if ev == "Stop" else \
        {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": text}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - never break the session
        pass
    sys.exit(0)
