# -*- coding: utf-8 -*-
"""ticketdesk CLI -- the agent-facing leg. Talks to the server over HTTP only (never edits
desk.json directly, so the page's undo stack stays valid) and starts the server if needed.

Tickets
  desk.py tree [KEYWORD] [--status open|partial|answered] [--priority P0|P1|P2|-]
  desk.py show T012 [--kids]                 everything on one ticket, incl. log -- read this before taking over
  desk.py add --title T [--parent T001] [--question Q] [--answer-title A] [--answer B]
              [--status open] [--priority P1] [--tags "a,b"] [--link "label|url"]... [--source S]
  desk.py update T012 [same fields] [--append-answer TEXT] [--add-link "label|url"]
  desk.py log T012 "what I did / found / where I am stuck" [--session ID] [--name NAME]
  desk.py move T012 --parent T001|ROOT [--before T013]
  desk.py todo [--all]                       open + partial tickets, P0 first
  desk.py handoff T012 T013 [--name NAME]    log a handoff on each ticket and print a brief for the next agent

Proposals (actions that need a human's approval)
  desk.py plist [--status pending|running|done|rejected]
  desk.py pshow P001
  desk.py padd --title T --tickets T012,T013 --summary S [--body B] [--approve-cmd CMD] [--priority P0] [--link ...]
  desk.py pupdate P001 [same fields]
  desk.py plog P001 "progress"
  desk.py papprove P001 [--by WHO]           pending -> running (marks approval only, never executes)
  desk.py preject P001 [--reason R]
  desk.py pdone P001 --summary S [--tickets T012]   archive the result into the linked tickets

Knowledge (reusable facts that tickets cite as K001)
  desk.py klist [KEYWORD] [--all]
  desk.py kshow K001
  desk.py kadd --title "one-sentence fact" [--body B] [--tags ...] [--link ...] [--source S]
  desk.py kupdate K001 [same fields]
  desk.py klog K001 "evidence / re-check"
  desk.py kretract K001 --reason R [--superseded-by K007]

  desk.py check                              dangling ids, tickets citing retracted knowledge (exit 1 if any)

Any text argument written as @path is read from that file.
Session id for logs: --session, else $DESK_SESSION, else $CLAUDE_SESSION_ID. Name: --name, else $DESK_SESSION_NAME,
else the current directory name.  Server: $DESK_URL (default http://127.0.0.1:8750), data dir $DESK_DATA.
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = os.environ.get("DESK_PORT", "8750")
BASE = os.environ.get("DESK_URL", "http://127.0.0.1:%s" % PORT).rstrip("/")
ST = {"open": "open", "partial": "partial", "answered": "answered"}
PST = ("pending", "running", "done", "rejected")
PRI = ("P0", "P1", "P2", "")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Desk-Client": "1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except Exception:  # noqa: BLE001
            msg = e.reason
        sys.exit("refused (%s): %s" % (e.code, msg))


def healthy():
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=1.5).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_server():
    if healthy():
        return
    if "DESK_URL" in os.environ:
        sys.exit("ticketdesk at %s is not answering" % BASE)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    argv = [str(pyw if pyw.exists() else exe), str(HERE / "server.py"), "--port", PORT]
    kw = {"cwd": str(HERE), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)
    for _ in range(40):
        time.sleep(0.25)
        if healthy():
            return
    sys.exit("ticketdesk did not start; run it by hand: python %s" % (HERE / "server.py"))


def txt(v):
    if isinstance(v, str) and v.startswith("@") and Path(v[1:]).is_file():
        return Path(v[1:]).read_text(encoding="utf-8")
    return v


def session():
    return os.environ.get("DESK_SESSION") or os.environ.get("CLAUDE_SESSION_ID") or ""


def who(a):
    sess = a.session if getattr(a, "session", None) is not None else session()
    name = getattr(a, "name", None) or os.environ.get("DESK_SESSION_NAME") or Path.cwd().name
    return {"session": sess, "name": name}


def pick(a, keys):
    b = {}
    for k in keys:
        v = getattr(a, k, None)
        if v is not None:
            b[k] = txt(v)
    if getattr(a, "link", None):
        b["links"] = "\n".join(a.link)
    return b


def pri(x):
    return ("[%s]" % x["priority"]) if x.get("priority") else ""


def entries(log, indent="  "):
    for e in log or []:
        sess = (" (%s)" % e["session"]) if e.get("session") else ""
        print("- %s  %s%s\n%s%s" % (e["ts"], e.get("name") or "", sess, indent, e["text"].replace("\n", "\n" + indent)))


def links(x):
    for l in x.get("links") or []:
        print("link: %s  %s" % (l["label"], l["url"]))


# ---------------------------------------------------------------- tickets

def tree(d, kw=None, status=None, priority=None):
    kids = {}
    for t in d["tickets"]:
        kids.setdefault(t["parent"] or "", []).append(t)
    for v in kids.values():
        v.sort(key=lambda t: t["order"])

    def hit(t):
        ok = ((not status or t["status"] == status) and (not kw or kw.lower() in json.dumps(t, ensure_ascii=False).lower())
              and (not priority or (t.get("priority") or "-") == priority))
        return ok or any(hit(c) for c in kids.get(t["id"], []))

    def go(p, dep):
        for t in kids.get(p, []):
            if hit(t):
                n = len(t.get("log") or [])
                print("%s%s [%s]%s %s%s" % ("  " * dep, t["id"], t["status"], pri(t), t["title"], ("  (log %d)" % n) if n else ""))
                go(t["id"], dep + 1)
    go("", 0)


def show(t, d, deep):
    print("%s [%s]%s %s" % (t["id"], t["status"], pri(t), t["title"]))
    print("parent: %s | tags: %s | source: %s | created %s | updated %s" % (
        t["parent"] or "(top)", ", ".join(t.get("tags") or []), t.get("source") or "", t.get("created"), t.get("updated")))
    links(t)
    print("\n## Question\n" + (t.get("question") or "(empty)"))
    print("\n## Answer: " + (t.get("answer_title") or "(none yet)") + "\n" + (t.get("answer") or ""))
    rel = [p for p in d["proposals"] if t["id"] in (p.get("tickets") or [])]
    if rel:
        print("\n## Proposals")
        for p in rel:
            print("- %s [%s] %s" % (p["id"], p["status"], p["title"]))
    print("\n## Log (%d)" % len(t.get("log") or []))
    entries(t.get("log"))
    ch = sorted([c for c in d["tickets"] if c["parent"] == t["id"]], key=lambda c: c["order"])
    if ch:
        print("\n## Children (%d)" % len(ch))
        for c in ch:
            print("- %s [%s]%s %s -- %s" % (c["id"], c["status"], pri(c), c["title"], c.get("answer_title") or ""))
        if deep:
            for c in ch:
                print("\n" + "=" * 60)
                show(c, d, True)


def todo(d, show_all):
    rank = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    xs = [t for t in d["tickets"] if t["status"] != "answered" and (show_all or t.get("priority") != "P2")]
    xs.sort(key=lambda t: (rank[t.get("priority") or ""], t["updated"] or ""), reverse=False)
    for t in xs:
        last = (t.get("log") or [{}])[-1]
        print("%-4s %s [%s] %s%s" % (t.get("priority") or "--", t["id"], t["status"], t["title"],
                                    ("\n       last log %s: %s" % (last["ts"], last["text"].splitlines()[0][:100])) if last else ""))
    pend = [p for p in d["proposals"] if p["status"] == "pending"]
    if pend:
        print("\nwaiting for approval:")
        for p in pend:
            print("     %s %s -> %s" % (p["id"], p["title"], ",".join(p.get("tickets") or [])))


def handoff(d, ids, a):
    w = who(a)
    for i in ids:
        call("/api/tickets/%s/log" % i, dict(w, text="Handoff: the next agent continues from this ticket (read `desk.py show %s --kids` first)." % i))
    print("Continue the work tracked in ticketdesk. Before doing anything else, run:\n")
    for i in ids:
        print("    python %s show %s --kids" % (Path(__file__).resolve().as_posix(), i))
    print("\nTreat the tickets as the source of truth: re-derive numbers instead of trusting the previous agent's summary,")
    print("log every substantive step with `desk.py log <ID> ...`, and record any next step as a ticket before acting on it.")
    print("\nTickets:")
    for i in ids:
        t = next(x for x in d["tickets"] if x["id"] == i)
        print("- %s [%s]%s %s -- %s" % (t["id"], t["status"], pri(t), t["title"], t.get("answer_title") or "no answer yet"))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="ticketdesk CLI", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def logger(name):
        p = sp.add_parser(name)
        p.add_argument("id"); p.add_argument("text"); p.add_argument("--session"); p.add_argument("--name")
        return p

    p = sp.add_parser("tree"); p.add_argument("kw", nargs="?"); p.add_argument("--status", choices=list(ST)); p.add_argument("--priority", choices=["P0", "P1", "P2", "-"])
    p = sp.add_parser("show"); p.add_argument("id"); p.add_argument("--kids", action="store_true")
    for name in ("add", "update"):
        p = sp.add_parser(name)
        if name == "update":
            p.add_argument("id"); p.add_argument("--append-answer"); p.add_argument("--add-link", action="append", help="label|url, appended (dedup by url)")
        else:
            p.add_argument("--parent")
        p.add_argument("--title"); p.add_argument("--question"); p.add_argument("--answer-title", dest="answer_title"); p.add_argument("--answer")
        p.add_argument("--status", choices=list(ST)); p.add_argument("--priority", choices=PRI); p.add_argument("--tags"); p.add_argument("--source")
        p.add_argument("--link", action="append", help="label|url, repeatable; replaces all links")
    logger("log")
    p = sp.add_parser("move"); p.add_argument("id"); p.add_argument("--parent", required=True); p.add_argument("--before")
    p = sp.add_parser("todo"); p.add_argument("--all", action="store_true", help="include P2")
    p = sp.add_parser("handoff"); p.add_argument("ids", nargs="+"); p.add_argument("--session"); p.add_argument("--name")

    p = sp.add_parser("plist"); p.add_argument("--status", choices=PST)
    p = sp.add_parser("pshow"); p.add_argument("id")
    for name in ("padd", "pupdate"):
        p = sp.add_parser(name)
        if name == "pupdate":
            p.add_argument("id")
        else:
            p.add_argument("--status", choices=("pending", "running"))
        p.add_argument("--title"); p.add_argument("--summary"); p.add_argument("--body"); p.add_argument("--tickets")
        p.add_argument("--approve-cmd", dest="approve_cmd"); p.add_argument("--priority", choices=PRI); p.add_argument("--tags")
        p.add_argument("--link", action="append")
    logger("plog")
    p = sp.add_parser("papprove"); p.add_argument("id"); p.add_argument("--by", default="cli")
    p = sp.add_parser("preject"); p.add_argument("id"); p.add_argument("--reason")
    p = sp.add_parser("pdone"); p.add_argument("id"); p.add_argument("--summary", required=True); p.add_argument("--tickets"); p.add_argument("--session"); p.add_argument("--name")

    p = sp.add_parser("klist"); p.add_argument("kw", nargs="?"); p.add_argument("--all", action="store_true", help="include retracted")
    p = sp.add_parser("kshow"); p.add_argument("id")
    for name in ("kadd", "kupdate"):
        p = sp.add_parser(name)
        if name == "kupdate":
            p.add_argument("id")
        p.add_argument("--title"); p.add_argument("--body"); p.add_argument("--tags"); p.add_argument("--source"); p.add_argument("--link", action="append")
    logger("klog")
    p = sp.add_parser("kretract"); p.add_argument("id"); p.add_argument("--reason", required=True); p.add_argument("--superseded-by", dest="superseded_by")
    p.add_argument("--session"); p.add_argument("--name")
    sp.add_parser("check")

    a = ap.parse_args()
    if hasattr(a, "id"):
        a.id = a.id.upper()
    ensure_server()
    c = a.cmd

    if c in ("tree", "show", "todo", "handoff"):
        d = call("/api/state")
        if c == "tree":
            return tree(d, a.kw, a.status, a.priority)
        if c == "todo":
            return todo(d, a.all)
        if c == "handoff":
            ids = [i.upper() for i in a.ids]
            missing = [i for i in ids if i not in {t["id"] for t in d["tickets"]}]
            if missing:
                sys.exit("no such ticket: " + ", ".join(missing))
            return handoff(d, ids, a)
        t = next((x for x in d["tickets"] if x["id"] == a.id), None) or sys.exit("no such ticket: " + a.id)
        return show(t, d, a.kids)
    if c == "add":
        b = pick(a, ("title", "question", "answer_title", "answer", "status", "priority", "tags", "source"))
        b["parent"] = a.parent.upper() if a.parent else None
        t = call("/api/tickets", b); print("created %s %s" % (t["id"], t["title"])); return
    if c == "update":
        b = pick(a, ("title", "question", "answer_title", "answer", "status", "priority", "tags", "source"))
        if a.add_link or a.append_answer:
            cur = call("/api/tickets/" + a.id)
            if a.add_link:
                if "links" in b:
                    sys.exit("use either --link (replace) or --add-link (append), not both")
                ls = list(cur.get("links") or [])
                seen = {l["url"] for l in ls}
                for x in a.add_link:
                    lab, _, url = x.partition("|")
                    url = (url or lab).strip()
                    if url not in seen:
                        ls.append({"label": lab.strip(), "url": url}); seen.add(url)
                b["links"] = ls
            if a.append_answer:
                b["answer"] = ((cur.get("answer") or "").rstrip() + "\n" + txt(a.append_answer)).strip()
        if not b:
            sys.exit("nothing to update")
        call("/api/tickets/" + a.id, b); print("updated %s: %s" % (a.id, ", ".join(b))); return
    if c == "log":
        e = call("/api/tickets/%s/log" % a.id, dict(who(a), text=txt(a.text)))
        print("logged on %s @%s session=%s" % (a.id, e["ts"], e["session"] or "(unknown)")); return
    if c == "move":
        par = None if a.parent.upper() == "ROOT" else a.parent.upper()
        call("/api/tickets/%s/move" % a.id, {"parent": par, "before": a.before.upper() if a.before else None})
        print("moved %s -> %s" % (a.id, par or "top level")); return

    if c == "plist":
        for x in call("/api/state")["proposals"]:
            if not a.status or x["status"] == a.status:
                print("%s [%s]%s %s -> %s" % (x["id"], x["status"], pri(x), x["title"], ",".join(x.get("tickets") or [])))
        return
    if c == "pshow":
        x = call("/api/proposals/" + a.id)
        print("%s [%s]%s %s\ntickets: %s | approve_cmd: %s | created %s | updated %s" % (
            x["id"], x["status"], pri(x), x["title"], ", ".join(x.get("tickets") or []), x.get("approve_cmd") or "", x.get("created"), x.get("updated")))
        links(x)
        print("\n## Summary\n%s\n\n## Body\n%s" % (x.get("summary") or "", x.get("body") or ""))
        if x.get("exec"):
            print("\n## Execution\n" + json.dumps(x["exec"], ensure_ascii=False, indent=1))
        if x.get("result"):
            print("\n## Result (archived to %s)\n%s" % (", ".join(x.get("archived_to") or []), x["result"]))
        print("\n## Log"); entries(x.get("log")); return
    if c in ("padd", "pupdate"):
        b = pick(a, ("title", "summary", "body", "tickets", "approve_cmd", "status", "priority", "tags"))
        if c == "padd":
            x = call("/api/proposals", b); print("created %s [%s] %s" % (x["id"], x["status"], x["title"])); return
        if not b:
            sys.exit("nothing to update")
        call("/api/proposals/" + a.id, b); print("updated %s: %s" % (a.id, ", ".join(b))); return
    if c == "plog":
        call("/api/proposals/%s/log" % a.id, dict(who(a), text=txt(a.text))); print("logged on " + a.id); return
    if c == "papprove":
        x = call("/api/proposals/%s/approve" % a.id, {"by": a.by}); print("approved %s -> %s" % (a.id, x["status"])); return
    if c == "preject":
        call("/api/proposals/%s/reject" % a.id, {"reason": a.reason or ""}); print("rejected " + a.id); return
    if c == "pdone":
        x = call("/api/proposals/%s/done" % a.id, dict(who(a), summary=txt(a.summary), tickets=a.tickets or ""))
        print("done %s, archived to %s" % (a.id, ", ".join(x["archived_to"]))); return

    if c == "klist":
        for k in call("/api/state")["knowledge"]:
            if (a.all or k["status"] == "current") and (not a.kw or a.kw.lower() in json.dumps(k, ensure_ascii=False).lower()):
                print("%s%s %s" % (k["id"], " [RETRACTED -> %s]" % (k.get("superseded_by") or "-") if k["status"] == "retracted" else "", k["title"]))
        return
    if c == "kshow":
        k = call("/api/knowledge/" + a.id)
        print("%s [%s] %s" % (k["id"], k["status"], k["title"]))
        if k.get("superseded_by"):
            print("superseded by " + k["superseded_by"])
        if k.get("supersedes"):
            print("supersedes " + ", ".join(k["supersedes"]))
        print("tags: %s | source: %s | cited by: %s" % (", ".join(k.get("tags") or []), k.get("source") or "", ", ".join(k.get("cited_by") or []) or "-"))
        links(k)
        print("\n" + (k.get("body") or "")); print("\n## Log"); entries(k.get("log")); return
    if c in ("kadd", "kupdate"):
        b = pick(a, ("title", "body", "tags", "source"))
        if c == "kadd":
            k = call("/api/knowledge", b); print("created %s %s" % (k["id"], k["title"])); return
        if not b:
            sys.exit("nothing to update")
        call("/api/knowledge/" + a.id, b); print("updated %s: %s" % (a.id, ", ".join(b))); return
    if c == "klog":
        call("/api/knowledge/%s/log" % a.id, dict(who(a), text=txt(a.text))); print("logged on " + a.id); return
    if c == "kretract":
        call("/api/knowledge/%s/retract" % a.id, dict(who(a), reason=txt(a.reason), superseded_by=a.superseded_by or ""))
        print("retracted %s%s" % (a.id, (" -> " + a.superseded_by.upper()) if a.superseded_by else "")); return
    if c == "check":
        issues = call("/api/check")
        for i in issues:
            print("%-16s %s" % (i["kind"], i["msg"]))
        print("%d issue(s)" % len(issues))
        sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
