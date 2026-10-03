# Working with ticketdesk: rules for humans and agents

These rules came out of daily use with several AI coding agents working on the same long-running
project in parallel and across many sessions. The failure they prevent: **what one session learned
was lost when the conversation ended or was compacted**, so the next session redid it, or worse, acted
on a summary that had quietly gone wrong.

The desk is the fix only if it is used consistently. Copy the parts you like into your agent
instructions (`AGENTS.md`, `CLAUDE.md`, a skill file, a system prompt…). A ready-made Claude Code skill
lives in [`integrations/claude-code/SKILL.md`](../integrations/claude-code/SKILL.md).

## 1. Tickets are the source of truth

- A ticket is the record that outlives any conversation. Any agent should be able to read a ticket
  (`desk.py show T012 --kids`) and take over without the original chat.
- **Start of work:** find the relevant tickets (`desk.py tree <keyword>`, `desk.py todo`) and read them,
  including the log.
- **End of work:** write conclusions and open items back. Something that only exists in the chat is
  not recorded.

## 2. Structure

- Keep **few top-level topics**. A new question goes under the right topic; open a new top-level
  ticket only for a genuinely new direction, and say so.
- **Reorganise by moving, never by renumbering** (`desk.py move`, `update --title`). Ids are quoted
  in other tickets, commits and handoffs; they must stay valid.

## 3. One card, one question

| Field | What goes in |
|---|---|
| title | the question itself |
| question | the requester's **original wording**, with when/where it was asked |
| answer_title | a one-line conclusion |
| answer | bullet points (`- …`), evidence, links |

- Status: **open** = no answer; **partial** = some readings, key facts missing; **answered** = settled
  (including "tested, did not hold").
- Open items are lines starting with `TODO:` that say **what fact is missing, how to get it, and what
  it costs** to find out. The page highlights them.
- A negative conclusion ("not worth it", "does not work") needs the same support as a positive one:
  write what it is based on and what result would flip it.

## 4. Log every substantive step

`desk.py log T012 "…"` records a timestamp, the session id and a session name. Write:

- what was done and what came out of it,
- **failed attempts** and how they were found to fail (the most valuable part, and the first thing a
  summary drops),
- where it is stuck,
- paths of the artifacts produced.

Pass `--session` (or set `DESK_SESSION`) so a log line can be traced back to the conversation that
wrote it.

## 5. Facts go into knowledge entries; tickets cite them

- A reusable fact (a measured number, a definition, "where the config lives") becomes a knowledge
  entry `K007`, and tickets cite it by id. Hovering an id on the page shows the entry.
- Never edit a fact silently. When it turns out wrong, **retract it with a reason** and point to its
  replacement (`desk.py kretract K003 --reason … --superseded-by K004`).
- `desk.py check` lists every ticket still citing a retracted entry and every id that points nowhere.
  Run it before relying on a conclusion and before handing off.

## 6. Ticket first, then act

Before starting any piece of work, yourself or by dispatching a sub-agent:

1. there is a ticket for it (create one; put the requester's words in `question`);
2. its log says who is starting, the expected duration and where the output will go;
3. a sub-agent's instructions name the ticket id and require it to write its result back there.

Afterwards set the status and answer. Work that was not done stays open with the reason, or what it is
waiting for. Something answered elsewhere is marked "merged into T0xx", not deleted.

## 7. Saying a next step means recording it

Whenever a reply says "next we should…", "TODO", "worth trying…", that item is written into a ticket
(new ticket or a `TODO:` line) **in the same turn**. Telling the human is not recording it.

The requester's key questions get the same treatment: log the question verbatim on the matching ticket
with the one-line answer, and if the answer corrects an earlier conclusion, append the correction to the
ticket's answer.

## 8. Priorities

| Priority | Meaning | What an agent does |
|---|---|---|
| **P0** | big and important | keeps going **without asking**; logs the story as it goes |
| **P1** | normal | normal work |
| **P2** | small or low value | parks it with one sentence on why |
| *(none)* | answered | — |

At the end of a session every unfinished item becomes a ticket with a priority.

## 9. "How we got here"

When an investigation that spanned several tickets lands on a cause or a decision, append a short
narrative to the ticket holding the conclusion: 3–6 lines, one per step (question → what was checked →
what it showed, with ids), ending in "⇒ conclusion" and a pointer to the next step. Each ticket along the
way gets a log line pointing to it.

## 10. Proposals: anything that needs a human's approval

Spending money, irreversible operations, production changes, anything outside the agent's mandate:

1. the agent files a proposal (`desk.py padd`) linked to its tickets, with a one-line summary, the
   plan, the risk and the rollback;
2. the human approves or rejects on the page;
3. if the server runs with `--allow-run` and `approve_cmd` is a script inside the scripts directory,
   approving runs it in the background and shows the exit code and output on the proposal. **Such a
   script checks its own preconditions and stops with a non-zero exit code on any failure**;
4. when done, `desk.py pdone P003 --summary …` archives the result into every linked ticket.

A verbal "go ahead" in chat can be recorded with `desk.py papprove`, but anything with real-world
consequences should be approved on the page by the human.

## 11. Handoff

When a conversation gets long or the work moves to another agent:

1. write the conclusions and open items into the tickets first;
2. `desk.py handoff T012 T015 --name "<this session>"` logs the handoff and prints a starting brief;
3. the new agent reads the tickets and **re-derives important numbers** instead of trusting the
   previous summary. Errors tend to come from an author continuing their own story, not from length.

## 12. Replying to the human

A human switching between several conversations forgets the context. A long answer starts with one
to three lines restating the question ("You asked: … (context: …)"), then the conclusion, then the
ticket ids touched with their links (`http://127.0.0.1:8750/#T012`).
