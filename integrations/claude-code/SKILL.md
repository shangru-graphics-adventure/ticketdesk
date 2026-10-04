---
name: ticketdesk
description: Local ticket desk (http://127.0.0.1:8750) shared by every session and agent — nested question/answer tickets with work logs, proposals awaiting human approval, and knowledge entries that tickets cite. Use when the user says "/ticketdesk", "ticket", "log this", "put this on the desk", "what's open", "handoff"; before starting any multi-step task (find and read the relevant tickets) and after finishing one (write results back).
---

# ticketdesk

Set `DESK` to wherever you cloned the repo, e.g. `python ~/tools/ticketdesk/desk.py`.
Only use the CLI or HTTP API — never edit `desk.json` by hand (the page keeps an undo stack).
The CLI starts the server if it is not running.

```
$DESK tree [keyword] [--status open|partial|answered] [--priority P0|P1|P2|-]
$DESK show T012 --kids
$DESK todo
$DESK add --title "…" --parent T001 [--question @file.md] [--priority P1] [--tags "a,b"] [--link "label|url"]
$DESK update T012 [--answer-title …] [--append-answer …] [--status answered] [--add-link "label|url"]
$DESK log T012 "what was done / found / failed / stuck" --session <session id> --name "<session name>"
$DESK move T012 --parent T001|ROOT [--before T013]
$DESK padd --title … --tickets T012 --summary … --body @plan.md [--approve-cmd script.sh] [--priority P0]
$DESK pdone P003 --summary @result.md
$DESK kadd --title "one-sentence fact" --body … ; $DESK kretract K003 --reason … --superseded-by K004
$DESK check
$DESK handoff T012 T015 --name "<session name>"
$DESK ask --title "Pin the cache key?" --situation "background, cost of each option" --options "pin|leave" --tickets T012 --session <session id>
$DESK needs                       # what is waiting on the user
$DESK eta T012 30                 # about 30 minutes left, clock starts now
$DESK jadd --title … --result "… **conclusion that holds** …" --story … --why … --goal … --next … --tickets "T012 (cache key)" --session <id> --name <name>
```

Long text: pass `@path`. Always pass `--session` on log commands (or export `DESK_SESSION`) so each
line traces back to its conversation.

## Rules

1. Tickets are the source of truth across sessions. Read the relevant tickets before working; write
   results and open items back before finishing. Chat-only information counts as lost.
2. One card = one question. Title = the question; question = the user's original words (when/where);
   answer_title = one-line conclusion; answer = bullets. Open items are `TODO:` lines naming the missing
   fact, how to get it and the cost. Negative conclusions state their basis and what would flip them.
3. Few top-level topics; file new questions under the right one. Reorganise with `move`; never renumber.
4. Log every substantive step: result, **failed attempts**, where it is stuck, artifact paths.
5. Reusable facts become knowledge entries (`kadd`) and tickets cite them as `K007`. Wrong facts are
   retracted with a reason and a replacement, never silently edited. Run `check` before relying on a
   conclusion or handing off.
6. Ticket first, then act: before starting work or dispatching a sub-agent, make sure a ticket exists and
   log who is starting, the expected duration and the output location. Sub-agent prompts name the ticket
   and require writing results back to it.
7. Any "next step" mentioned in a reply is written into a ticket in the same turn. The user's key
   questions are logged verbatim on the matching ticket with the answer.
8. Priorities: P0 = keep going without asking, log the story; P1 = normal; P2 = park with one sentence
   why. Every unfinished item at the end of a session becomes a ticket with a priority.
9. Anything that needs the user's approval (money, irreversible, production) becomes a proposal
   (`padd`); the user approves on the page. Approval scripts check their own preconditions and exit
   non-zero on failure. Finish with `pdone`, which archives the result into the linked tickets.
10. When an investigation lands, append a 3–6 line "How we got here" to the conclusion ticket.
11. Handoff: write tickets first, then `handoff`; the new agent re-derives key numbers.
12. Long replies start by restating the user's question in 1–3 lines, and end with the ticket ids
    touched and their links (`http://127.0.0.1:8750/#T012`).
13. A decision only the user can make, and that blocks you, is an `ask` (with options when there are
    any) instead of a question buried in a long reply. Pass `--session` so the answer comes back to
    you: typed in when you are idle, or delivered by the inbox hook while you are working.
14. When you start a piece of work with a known rough size, set the ETA clock once (`eta`).
15. When a piece of work wraps up (delivered, handed off, session ending), write one journal entry in
    plain words: story, why, goal, result, next; wrap conclusions that hold in `**…**`; every ticket id
    you mention carries a few words in parentheses: `T012 (cache key)`.
