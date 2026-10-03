# -*- coding: utf-8 -*-
"""End-to-end tests: start a real server on a free port with a temp data dir and drive it over HTTP
(and through the CLI). Run:  python -m unittest discover -s tests -v"""
import json, os, shutil, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Desk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ticketdesk_test_")
        cls.scripts = os.path.join(cls.tmp, "scripts")
        os.makedirs(cls.scripts)
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--data", cls.tmp, "--port", str(cls.port),
                                     "--allow-run", "--scripts-dir", cls.scripts], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        t0 = time.time()
        while True:
            try:
                urllib.request.urlopen(cls.base + "/api/health", timeout=0.5).read()
                break
            except Exception:  # noqa: BLE001
                if time.time() - t0 > 10:
                    raise RuntimeError("server did not start: %s" % cls.proc.stderr.read())
                time.sleep(0.1)
        cls.startup = time.time() - t0

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- helpers
    def req(self, path, body=None, headers=None, expect=200):
        h = {"Content-Type": "application/json", "X-Desk-Client": "1"}
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                code, out = resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            code, out = e.code, json.load(e)
        self.assertEqual(code, expect, "%s -> %s %s" % (path, code, out))
        return out

    def cli(self, *args, expect=0):
        env = dict(os.environ, DESK_URL=self.base, DESK_SESSION="test-session", DESK_SESSION_NAME="unit test", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "desk.py")] + list(args), capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r.stdout + r.stderr

    # ---- security guards (each paired with a request that must pass)
    def test_csrf_header_required(self):
        self.req("/api/tickets", {"title": "x"}, headers={"X-Desk-Client": "0"}, expect=403)
        self.req("/api/tickets", {"title": "guard control"})

    def test_host_header_checked(self):
        self.req("/api/state", headers={"Host": "evil.example:%d" % self.port}, expect=403)
        self.req("/api/state")

    # ---- tickets
    def test_ticket_lifecycle(self):
        a = self.req("/api/tickets", {"title": "Why is the build slow?", "priority": "P1", "tags": "build, ci"})
        self.assertRegex(a["id"], r"^T\d{3}$")
        self.assertEqual(a["tags"], ["build", "ci"])
        b = self.req("/api/tickets", {"title": "Measure cache hit rate", "parent": a["id"]})
        self.assertEqual(b["parent"], a["id"])
        e = self.req("/api/tickets/%s/log" % b["id"], {"text": "hit rate 12%", "session": "s1", "name": "n1"})
        self.assertTrue(e["ts"])
        self.req("/api/tickets/%s" % b["id"], {"status": "answered", "answer_title": "cache is cold"})
        got = self.req("/api/tickets/" + b["id"])
        self.assertEqual((got["status"], len(got["log"])), ("answered", 1))
        # validation failures are reported, not swallowed
        self.req("/api/tickets/%s" % b["id"], {"status": "maybe"}, expect=400)
        self.req("/api/tickets/%s" % b["id"], {"priority": "P9"}, expect=400)
        self.req("/api/tickets", {"title": "   "}, expect=400)
        self.req("/api/tickets/%s/log" % b["id"], {"text": " "}, expect=400)
        self.req("/api/tickets", {"title": "x", "links": [{"url": "u%d" % i} for i in range(201)]}, expect=400)
        self.req("/api/tickets/T999", {"title": "x"}, expect=404)

    def test_move_rejects_cycles_and_delete_lifts_children(self):
        a = self.req("/api/tickets", {"title": "A"})
        b = self.req("/api/tickets", {"title": "B", "parent": a["id"]})
        c = self.req("/api/tickets", {"title": "C", "parent": b["id"]})
        self.req("/api/tickets/%s/move" % a["id"], {"parent": c["id"]}, expect=400)
        self.req("/api/tickets/%s/move" % a["id"], {"parent": a["id"]}, expect=400)
        self.req("/api/tickets/%s/move" % c["id"], {"parent": a["id"], "before": b["id"]})
        kids = [t for t in self.req("/api/state")["tickets"] if t["parent"] == a["id"]]
        self.assertEqual([t["id"] for t in sorted(kids, key=lambda t: t["order"])], [c["id"], b["id"]])
        self.req("/api/tickets/%s/delete" % a["id"], {})
        self.assertIsNone(self.req("/api/tickets/" + c["id"])["parent"])

    def test_undo(self):
        a = self.req("/api/tickets", {"title": "before"})
        self.req("/api/tickets/" + a["id"], {"title": "after"})
        self.req("/api/undo", {})
        self.assertEqual(self.req("/api/tickets/" + a["id"])["title"], "before")

    # ---- proposals
    def test_proposal_flow_and_archive(self):
        t = self.req("/api/tickets", {"title": "Upgrade the database"})
        self.req("/api/proposals", {"title": "x", "tickets": "T998"}, expect=400)
        p = self.req("/api/proposals", {"title": "Run the migration", "tickets": t["id"], "approve_cmd": "migrate.sh"})
        self.assertEqual(p["status"], "pending")
        self.req("/api/proposals/" + p["id"], {"status": "done"}, expect=400)          # status only via actions
        self.req("/api/proposals/%s/done" % p["id"], {"summary": ""}, expect=400)
        x = self.req("/api/proposals/%s/approve" % p["id"], {"by": "test"})
        self.assertEqual(x["status"], "running")
        self.req("/api/proposals/%s/approve" % p["id"], {}, expect=400)                # not pending any more
        self.req("/api/proposals/%s/done" % p["id"], {"summary": "migrated, 0 errors", "name": "agent"})
        log = self.req("/api/tickets/" + t["id"])["log"]
        self.assertIn("[proposal %s done]" % p["id"], log[-1]["text"])

    def test_run_executes_only_scripts_inside_scripts_dir(self):
        t = self.req("/api/tickets", {"title": "run target"})
        ok = os.path.join(self.scripts, "ok.py")
        with open(ok, "w") as f:
            f.write("print('hello from approval')\n")
        bad = os.path.join(self.scripts, "fail.py")
        with open(bad, "w") as f:
            f.write("import sys; print('boom'); sys.exit(3)\n")
        outside = os.path.join(self.tmp, "outside.py")
        with open(outside, "w") as f:
            f.write("open(%r, 'w').write('ran')\n" % os.path.join(self.tmp, "SHOULD_NOT_EXIST").replace("\\", "/"))

        def run(cmd):
            p = self.req("/api/proposals", {"title": "run " + cmd, "tickets": t["id"], "approve_cmd": cmd})
            self.req("/api/proposals/%s/run" % p["id"], {})
            for _ in range(100):
                x = self.req("/api/proposals/" + p["id"])
                if x["exec"]["state"] != "running":
                    return x
                time.sleep(0.1)
            self.fail("run did not finish")

        x = run("ok.py")
        self.assertEqual((x["exec"]["state"], x["exec"]["rc"]), ("ok", 0))
        self.assertIn("hello from approval", x["exec"]["tail"])
        x = run(bad)
        self.assertEqual((x["exec"]["state"], x["exec"]["rc"]), ("failed", 3))
        x = run(outside)                                   # outside the scripts dir: approved, never executed
        self.assertEqual((x["status"], x["exec"]["state"]), ("running", "none"))
        x = run("../outside.py")
        self.assertEqual(x["exec"]["state"], "none")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "SHOULD_NOT_EXIST")))

    # ---- knowledge + consistency check
    def test_knowledge_retract_and_check(self):
        k1 = self.req("/api/knowledge", {"title": "The API rate limit is 100 req/min"})
        k2 = self.req("/api/knowledge", {"title": "The API rate limit is 600 req/min"})
        t = self.req("/api/tickets", {"title": "Plan the crawler", "answer": "- budget follows %s" % k1["id"]})
        self.req("/api/knowledge/%s/retract" % k1["id"], {"reason": ""}, expect=400)
        self.req("/api/knowledge/%s/retract" % k1["id"], {"reason": "x", "superseded_by": k1["id"]}, expect=400)
        self.req("/api/knowledge/%s/retract" % k1["id"], {"reason": "docs changed", "superseded_by": k2["id"]})
        self.assertIn(k1["id"], self.req("/api/knowledge/" + k2["id"])["supersedes"])
        self.assertIn(t["id"], self.req("/api/knowledge/" + k1["id"])["cited_by"])
        kinds = {(i["kind"], i["id"], i["ref"]) for i in self.req("/api/check")}
        self.assertIn(("cites_retracted", t["id"], k1["id"]), kinds)
        self.req("/api/tickets/" + t["id"], {"answer": "- budget follows %s, see T999" % k2["id"]})
        kinds = {(i["kind"], i["id"], i["ref"]) for i in self.req("/api/check")}
        self.assertNotIn(("cites_retracted", t["id"], k1["id"]), kinds)
        self.assertIn(("dangling_ref", t["id"], "T999"), kinds)

    # ---- CLI
    def test_cli_roundtrip(self):
        out = self.cli("add", "--title", "CLI ticket", "--priority", "P0", "--question", "from the cli")
        tid = out.split()[1]
        self.cli("log", tid, "did a thing")
        self.cli("update", tid, "--append-answer", "- first finding", "--add-link", "docs|https://example.com")
        shown = self.cli("show", tid)
        for s in ("CLI ticket", "did a thing", "test-session", "unit test", "first finding", "https://example.com"):
            self.assertIn(s, shown)
        self.assertIn(tid, self.cli("todo"))
        self.assertIn(tid, self.cli("tree", "CLI"))
        self.assertIn("refused (400)", self.cli("update", tid, "--priority", "P0", "--title", " ", expect=1))
        brief = self.cli("handoff", tid, "--name", "old agent")
        self.assertIn("show %s --kids" % tid, brief)
        k = self.cli("kadd", "--title", "a fact").split()[1]
        self.cli("kretract", k, "--reason", "wrong")
        self.cli("update", tid, "--append-answer", "relies on " + k)
        self.assertIn("cites retracted", self.cli("check", expect=1))

    def test_page_served_and_fast(self):
        t0 = time.time()
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            html = r.read().decode()
        self.assertIn("ticketdesk", html)
        for _ in range(50):
            self.req("/api/tickets", {"title": "bulk"})
        t1 = time.time()
        self.req("/api/state")
        self.assertLess(time.time() - t1, 0.5, "state read too slow")
        self.assertLess(t1 - t0, 15, "50 writes too slow")
        self.assertLess(self.startup, 10)


if __name__ == "__main__":
    unittest.main()
