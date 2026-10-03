# -*- coding: utf-8 -*-
"""Fill a desk with a small, realistic example so you can click around.

    python server.py --data ./demo-data --port 8751      # in one terminal
    DESK_URL=http://127.0.0.1:8751 python examples/demo.py
"""
import json, os, urllib.request

BASE = os.environ.get("DESK_URL", "http://127.0.0.1:8750").rstrip("/")


def post(path, body):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Desk-Client": "1"})
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)


def log(rid, text, name="agent: perf-investigation", session="sess-4f1c"):
    coll = {"T": "tickets", "P": "proposals", "K": "knowledge"}[rid[0]]
    post("/api/%s/%s/log" % (coll, rid), {"text": text, "name": name, "session": session})


k1 = post("/api/knowledge", {"title": "CI runners have 4 vCPUs and 16 GB RAM", "tags": "ci",
                             "body": "Read from the runner image docs and confirmed with `nproc` inside a job.\nHolds for the default runner pool only."})
k2 = post("/api/knowledge", {"title": "Dependency cache is keyed on the lockfile hash", "tags": "ci, cache",
                             "body": "Any lockfile change invalidates the whole cache."})
k3 = post("/api/knowledge", {"title": "Test suite takes 6 minutes on CI", "tags": "ci"})
k4 = post("/api/knowledge", {"title": "Test suite takes 9 minutes on CI (p50 over the last 50 runs)", "tags": "ci",
                             "body": "The earlier 6-minute figure came from a single run on a warm cache."})
post("/api/knowledge/%s/retract" % k3["id"], {"reason": "single warm-cache run, not representative", "superseded_by": k4["id"], "name": "reviewer"})

top = post("/api/tickets", {"title": "Build & CI performance", "priority": "P1", "tags": "ci",
                            "question": "Umbrella for everything about slow builds. New questions about CI speed go under here."})
t1 = post("/api/tickets", {"title": "Why did CI time double last month?", "parent": top["id"], "priority": "P0", "status": "partial", "tags": "ci",
                           "question": "\"CI used to take about 10 minutes, now it is over 20. What changed?\" (asked in the weekly sync)",
                           "answer_title": "Mostly cache misses: the lockfile now changes in almost every PR",
                           "answer": "- Test time itself is %s; it is not the main driver.\n- Cache is keyed on the lockfile (%s), and a bot bumps a dependency daily.\n- TODO: confirm by replaying 20 recent runs with the cache key pinned. Cost: ~1 hour of runner time." % (k4["id"], k2["id"])})
t2 = post("/api/tickets", {"title": "Can we split the test suite across runners?", "parent": top["id"], "priority": "P1", "tags": "ci, tests",
                           "question": "With %s, would 3-way sharding bring tests under 4 minutes?" % k1["id"]})
t3 = post("/api/tickets", {"title": "Flaky integration test in the payments module", "parent": top["id"], "priority": "P2", "status": "open", "tags": "tests",
                           "question": "Fails about 1 in 30 runs. Parked: low impact, retries hide it."})
t4 = post("/api/tickets", {"title": "Does the Docker layer order matter?", "parent": t1["id"], "status": "answered", "tags": "docker",
                           "answer_title": "Yes: copying the source before installing deps busted the layer cache",
                           "answer": "- Moved `COPY . .` after the install step; image build went from 4m10s to 50s.\n- Fixed in the build file; see the log for the run ids."})
docs = post("/api/tickets", {"title": "Onboarding docs", "priority": "P2", "tags": "docs",
                             "question": "Is the local setup guide still correct for new laptops? It still quotes %s." % k3["id"]})

log(t1["id"], "Pulled timings for the last 50 runs. Median 21m; tests 9m (%s), dependency install 8m." % k4["id"])
log(t1["id"], "Failed attempt: assumed the slowdown came from the new lint step. Removing it saved only 40s, so that is not it.")
log(t1["id"], "Cache hit rate fell from 92%% to 18%% after the dependency bot was enabled (%s)." % t4["id"])
log(t4["id"], "Reordered the build file and compared 5 builds before / after.", name="agent: docker")
log(t2["id"], "Not started. Waiting for %s to settle first, because sharding a cold cache just multiplies misses." % t1["id"])

p1 = post("/api/proposals", {"title": "Batch dependency-bot updates into one weekly PR", "tickets": t1["id"], "priority": "P0",
                             "summary": "Stop invalidating the CI cache every day; expected to recover most of the 10 lost minutes.",
                             "body": "- Change the bot schedule from daily to weekly and group updates.\n- Risk: security patches wait up to a week; keep the bot's security updates immediate.\n- Rollback: revert the bot config.",
                             "approve_cmd": "batch_bot_updates.sh"})
p2 = post("/api/proposals", {"title": "Buy larger CI runners", "tickets": t2["id"], "priority": "P2",
                             "summary": "Costs money; only worth it if sharding does not get us under 10 minutes.",
                             "approve_cmd": "a human changes the CI plan in the billing settings"})
post("/api/proposals/%s/reject" % p2["id"], {"reason": "revisit after %s" % t2["id"]})
print("demo data loaded: open %s/" % BASE)
