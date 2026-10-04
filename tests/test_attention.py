# -*- coding: utf-8 -*-
"""Asks / "needs you" / inbox hook / ETA clock / journal, end to end against a real server.
Run:  python -m unittest discover -s tests -v"""
import json, os, subprocess, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_api  # noqa: E402  (module import: a bare Desk here would make unittest run its tests twice)
ROOT = test_api.ROOT

SID = "aaaaaaaa-1111-4111-8111-111111111111"      # not a live session: answers to it must be queued for the inbox hook


class Attention(unittest.TestCase):
    req = test_api.Desk.req
    cli = test_api.Desk.cli

    @classmethod
    def setUpClass(cls):
        test_api.Desk.setUpClass.__func__(cls)

    def setUp(self):
        if not hasattr(type(self), "t1"):
            type(self).t1 = self.req("/api/tickets", {"title": "Why did CI double?"})["id"]

    @classmethod
    def tearDownClass(cls):
        test_api.Desk.tearDownClass.__func__(cls)

    def hook(self, sid, event):
        env = dict(os.environ, DESK_DATA=self.tmp, PYTHONIOENCODING="utf-8")
        t0 = time.time()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "integrations", "claude-code", "inbox_hook.py")],
                           input=json.dumps({"session_id": sid, "hook_event_name": event}).encode(), capture_output=True, env=env, timeout=20)
        self.assertEqual(r.returncode, 0)
        return r.stdout.decode("utf-8"), time.time() - t0

    # ---- asks
    def test_ask_validation(self):
        self.req("/api/asks", {"title": ""}, expect=400)
        self.req("/api/asks", {"title": "x", "tickets": "T999"}, expect=400)
        self.req("/api/asks", {"title": "x", "options": "|".join("o%d" % i for i in range(13))}, expect=400)
        self.req("/api/asks", {"title": "x", "priority": "P9"}, expect=400)
        self.req("/api/asks", {"title": "x"}, headers={"X-Desk-Client": ""}, expect=403)     # CSRF guard applies here too

    def test_ask_answer_queue_and_hook(self):
        a = self.req("/api/asks", {"title": "Pin the cache key?", "situation": "8 of the extra 10 minutes", "options": "pin|leave",
                                   "tickets": self.t1, "from_sid": SID, "from_name": "ci-speed", "priority": "P0"})
        self.assertEqual(a["status"], "open")
        att = self.req("/api/attention")
        self.assertIn(a["id"], att["sig"])
        self.assertTrue(any(i["id"] == a["id"] and i["options"] == ["pin", "leave"] for i in att["items"]))
        self.assertIn("[ask %s]" % a["id"], self.req("/api/tickets/" + self.t1)["log"][-1]["text"])
        self.req("/api/asks/%s/answer" % a["id"], {"choice": "maybe"}, expect=400)            # not one of the options
        self.req("/api/asks/%s/answer" % a["id"], {}, expect=400)                              # empty
        r = self.req("/api/asks/%s/answer" % a["id"], {"choice": "pin", "text": "and tell me the new time"})
        self.assertEqual(r["status"], "answered")
        self.assertEqual(r["delivered"]["mode"], "queued", r["delivered"])                      # the asker is not live
        self.req("/api/asks/%s/answer" % a["id"], {"choice": "leave"}, expect=400)             # already answered
        self.assertNotIn(a["id"], self.req("/api/attention")["sig"])
        self.assertIn("[answer %s]" % a["id"], self.req("/api/tickets/" + self.t1)["log"][-1]["text"])
        q = self.req("/api/sessions/%s/messages" % SID)
        self.assertTrue(any(m.get("mode") == "queued" and "chose \"pin\"" in m["text"] for m in q), q)
        # positive control first: another session's inbox is empty, so the hook stays silent
        out, _ = self.hook("bbbbbbbb-1111-4111-8111-111111111111", "PostToolUse")
        self.assertEqual(out, "")
        out, sec = self.hook(SID, "PostToolUse")
        d = json.loads(out)
        self.assertIn("chose \"pin\"", d["hookSpecificOutput"]["additionalContext"])
        self.assertLess(sec, 5)
        out, _ = self.hook(SID, "PostToolUse")                                                  # delivered once only
        self.assertEqual(out, "")
        self.assertTrue(all(m.get("mode") != "queued" for m in self.req("/api/sessions/%s/messages" % SID)))

    def test_stop_blocks_and_unsend(self):
        sid = "cccccccc-1111-4111-8111-111111111111"
        for txt in ("first", "second"):
            a = self.req("/api/asks", {"title": "Q " + txt, "from_sid": sid})
            self.req("/api/asks/%s/answer" % a["id"], {"text": txt})
        q = [m for m in self.req("/api/sessions/%s/messages" % sid) if m.get("mode") == "queued"]
        self.assertEqual(len(q), 2)
        self.assertTrue(self.req("/api/sessions/%s/unsend" % sid, {"id": q[0]["id"]})["ok"])
        self.req("/api/sessions/%s/unsend" % sid, {"id": q[0]["id"]}, expect=409)               # already taken back
        d = json.loads(self.hook(sid, "Stop")[0])
        self.assertEqual(d["decision"], "block")
        self.assertIn("second", d["reason"])
        self.assertNotIn("Q first", d["reason"])

    def test_withdraw(self):
        a = self.req("/api/asks", {"title": "obsolete"})
        self.assertEqual(self.req("/api/asks/%s/withdraw" % a["id"], {})["status"], "withdrawn")
        self.assertNotIn(a["id"], self.req("/api/attention")["sig"])
        self.req("/api/asks/%s/withdraw" % a["id"], {}, expect=400)

    def test_answer_without_session_is_recorded(self):
        a = self.req("/api/asks", {"title": "no asker"})
        r = self.req("/api/asks/%s/answer" % a["id"], {"text": "ok"})
        self.assertFalse(r["delivered"]["ok"])

    # ---- ETA clock
    def test_eta(self):
        t = self.req("/api/tickets", {"title": "eta card"})["id"]
        self.req("/api/tickets/" + t, {"eta": -5}, expect=400)
        self.req("/api/tickets/" + t, {"eta": "soon"}, expect=400)
        x = self.req("/api/tickets/" + t, {"eta": 30})
        self.assertEqual(x["eta"]["min"], 30)
        self.assertLess(abs(x["eta"]["start"] - time.time()), 30)
        self.assertNotIn(t, [o["id"] for o in self.req("/api/attention")["overdue"]])
        self.req("/api/tickets/" + t, {"eta": {"min": 1, "start": time.time() - 600}})          # 10 min into a 1-min estimate
        self.assertIn(t, [o["id"] for o in self.req("/api/attention")["overdue"]])
        self.assertIsNone(self.req("/api/tickets/" + t, {"eta": 0})["eta"])

    # ---- journal
    def test_journal(self):
        self.req("/api/journal", {"title": "no result"}, expect=400)
        j = self.req("/api/journal", {"title": "CI cache", "result": "**pinned the key**, 21 -> 12 min", "tickets": ["T001 (CI time)"],
                                      "start": "10-04 09:10", "end": "10-04 10:40"})
        self.assertTrue(j["id"].startswith("J"))
        self.assertEqual(j["tickets"], "T001 (CI time)")
        self.assertEqual(self.req("/api/journal/" + j["id"], {"next": "watch a week"})["next"], "watch a week")
        self.req("/api/journal/" + j["id"], {"title": ""}, expect=400)
        self.req("/api/tickets/" + j["id"], {"title": "x"}, expect=404)                         # J id under the tickets route
        self.assertTrue(self.req("/api/journal/%s/delete" % j["id"], {})["ok"])
        self.assertNotIn(j["id"], [x["id"] for x in self.req("/api/state")["journal"]])

    # ---- CLI
    def test_cli(self):
        out = self.cli("ask", "--title", "Ship it?", "--options", "yes|no", "--session", SID)
        aid = out.split()[1].rstrip(":")
        self.assertIn(aid, self.cli("needs"))
        self.assertIn("answered", self.cli("answer", aid, "--choice", "yes"))
        self.assertIn("yes", self.cli("alist", "--all"))
        t = self.req("/api/tickets", {"title": "cli eta"})["id"]
        self.assertIn("30 min", self.cli("eta", t, "30"))
        self.assertIn("cleared", self.cli("eta", t, "0"))
        self.assertIn("journal J", self.cli("jadd", "--title", "cli entry", "--result", "done"))
        self.assertIn("cli entry", self.cli("jlist", "cli"))


if __name__ == "__main__":
    unittest.main()
