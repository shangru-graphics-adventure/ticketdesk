# -*- coding: utf-8 -*-
"""Sessions panel: a fake ~/.claude (session files + a transcript) and a real server pointed at it with CLAUDE_DIR.
Run:  python -m unittest discover -s tests -v"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_api import free_port  # noqa: E402

SID = "11111111-2222-4333-8444-555555555555"
DEAD = "99999999-2222-4333-8444-555555555555"


def rec(**k):
    return json.dumps(k, ensure_ascii=False)


def transcript():
    t = lambda s: "2026-01-01T10:%02d:00Z" % s  # noqa: E731
    return "\n".join([
        rec(type="ai-title", aiTitle="CI speed"),
        rec(type="user", timestamp=t(0), message={"role": "user", "content": "Why did CI double? See T001, and also 2."}),
        rec(type="assistant", timestamp=t(1), message={"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "name": "Bash", "input": {}}]}),
        rec(type="user", timestamp=t(1), message={"content": [{"type": "tool_result", "content": "ok"}]}),
        rec(type="attachment", timestamp=t(2), attachment={"type": "queued_command", "prompt": "while you're at it: is the cache hit rate under 30%?",
                                                           "commandMode": "prompt", "timestamp": t(2)}),
        rec(type="assistant", timestamp=t(3), message={"content": [{"type": "text", "text": "You asked why CI doubled.\n\n- Tests take 9 min\n- Deps take 8 min\n\n```\nlog\n```"}]}),
        rec(type="system", subtype="turn_duration", timestamp=t(4), durationMs=1000),
        rec(type="system", subtype="away_summary", timestamp=t(5), content="CI doubled because deps are no longer cached. (disable recaps in /config)"),
    ]) + "\n"


class Sessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ticketdesk_sess_")
        cls.claude = os.path.join(cls.tmp, "claude")
        os.makedirs(os.path.join(cls.claude, "sessions"))
        os.makedirs(os.path.join(cls.claude, "projects", "proj"))
        cls.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])        # stands in for a live claude process
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()                         # positive control: a dead pid
        for sid, pid, st in ((SID, cls.child.pid, "idle"), (DEAD, dead.pid, "idle")):
            with open(os.path.join(cls.claude, "sessions", "%d.json" % pid), "w", encoding="utf-8") as f:
                json.dump({"pid": pid, "sessionId": sid, "cwd": "/work", "kind": "interactive", "name": "demo-1", "status": st,
                           "startedAt": int(time.time() * 1000)}, f)
        with open(os.path.join(cls.claude, "projects", "proj", SID + ".jsonl"), "w", encoding="utf-8") as f:
            f.write(transcript())
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = dict(os.environ, CLAUDE_DIR=cls.claude)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--data", os.path.join(cls.tmp, "data"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
        t0 = time.time()
        while True:
            try:
                urllib.request.urlopen(cls.base + "/api/health", timeout=0.5).read()
                break
            except Exception:  # noqa: BLE001
                if time.time() - t0 > 10:
                    raise RuntimeError("server did not start")
                time.sleep(0.1)
        for title in ("CI time", "Cache"):
            cls.post(cls, "/api/tickets", {"title": title})

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5)
        cls.child.kill(); cls.child.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, body, expect=200):
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Desk-Client": "1"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                code, out = resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            code, out = e.code, json.load(e)
        if expect is not None:
            assert code == expect, "%s -> %s %s" % (path, code, out)
        return out

    def get(self):
        t0 = time.time()
        with urllib.request.urlopen(self.base + "/api/sessions", timeout=10) as r:
            d = json.load(r)
        return d, time.time() - t0

    def test_lists_live_sessions_only(self):
        d, dt = self.get()
        self.assertEqual([r["sid"] for r in d["rows"]], [SID])   # the dead pid is left out
        self.assertLess(dt, 2.0)

    def test_tasks_refs_and_points(self):
        w = self.get()[0]["rows"][0]["work"]
        self.assertEqual(w["title"], "CI speed")
        self.assertFalse(w["turn_open"])
        first, mid = w["tasks"][0], w["tasks"][1]
        self.assertEqual((first["via"], first["refs"], first["guess"]), ("prompt", ["T001"], ["T002"]))
        self.assertEqual((mid["via"], mid["refs"], mid["guess"]), ("mid-turn", [], []))   # "30%" is a unit, not a ticket
        self.assertEqual(first["state"], "answered")
        self.assertEqual(w["points_src"], "recap")
        self.assertEqual(w["points"], ["CI doubled because deps are no longer cached."])

    def test_points_from_reply_without_recap(self):
        import sessions
        self.assertEqual(sessions.points_of("You asked why CI doubled.\n\n- Tests take 9 min\n- Deps take 8 min\n\n```\nx\n```"),
                         ["You asked why CI doubled.", "Tests take 9 min", "Deps take 8 min"])

    def test_ref_parsing(self):
        import sessions
        r = sessions.refs_in("see t1 and 0002 but not 30% or 5s", {"T001", "T002", "T030", "T005"})
        self.assertEqual(r, (["T001"], ["T002"]))

    def test_reply_and_focus_guards(self):
        self.assertIn("error", self.post("/api/sessions/%s/reply" % SID, {"text": ""}, expect=400))
        self.assertIn("error", self.post("/api/sessions/%s/reply" % DEAD, {"text": "hi"}, expect=400))       # not running
        self.post("/api/sessions/not-a-session/reply", {"text": "hi"}, expect=404)
        r = urllib.request.Request(self.base + "/api/sessions/%s/reply" % SID, data=b'{"text":"x"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:                                                 # CSRF header still required
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
