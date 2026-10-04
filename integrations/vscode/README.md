# VS Code + Claude Code: which tab is waiting for you

Three small pieces, all optional, for people who run several Claude Code sessions in VS Code terminal tabs.

| Piece | What it does |
|---|---|
| `tab_mark.py` (Claude Code hook) | A tab whose session has finished its turn and is waiting for you gets a **▶** in front of its name; the ▶ goes away when you send the next prompt. Optional short chime. Tabs never move. |
| `bridge/` (VS Code extension) | Lets the ticketdesk **Sessions** tab bring a session's terminal into view (*Go to terminal*) and type a reply into an idle session (*Send*). |
| `exthost-reloader/` (VS Code extension) | Restarts the extension host when `~/.claude/scripts/vscode_reload_request` is touched, so an updated local extension loads without *Developer: Restart Extension Host*. Terminals and running sessions are not affected. |

## Why a ▶ and not a divider or reordering

We tried keeping "waiting" tabs below a divider tab. VS Code has no API to reorder terminal tabs: the only way commands
can move one is to send it to the editor area and back (`terminal.moveToEditor` + `moveToTerminalPanel`), which flashes
the whole window on every move. Mouse drag-and-drop uses an internal call that extensions cannot reach.

Renaming through the API (`workbench.action.terminal.renameWithArg`) is not much better: it only acts on the *active*
terminal, so marking a background tab means switching to it and back, and after an API rename the tab ignores any title
the process sets later.

`tab_mark.py` sets the **console title** of the claude process instead (Windows: `AttachConsole` + `SetConsoleTitleW`;
macOS/Linux: an `ESC ] 0 ; … BEL` sequence written to its tty). VS Code shows the process title as the tab name with no
VS Code call at all: no switching, no moving, no flicker. Two caveats:

- Claude Code sets its own title too. Set `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` (below). This only affects sessions
  started after the change.
- A tab that was renamed by hand or through the API keeps that static name and will not show the ▶. New tabs work.

## Setup

1. Hook and env var in `~/.claude/settings.json` (merge with what you have; use `python3` on macOS/Linux):

```json
{
  "env": { "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1" },
  "hooks": {
    "Stop":             [{ "hooks": [{ "type": "command", "command": "python /path/to/ticketdesk/integrations/vscode/tab_mark.py Stop" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "python /path/to/ticketdesk/integrations/vscode/tab_mark.py Notification" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python /path/to/ticketdesk/integrations/vscode/tab_mark.py UserPromptSubmit" }] }]
  }
}
```

   `TAB_MARK_SILENT=1` turns the chime off. Name a tab yourself with
   `python tab_mark.py label <session id> "Fix CI cache [s1]"` (the ▶ state is kept).

2. Extensions: copy `bridge/` and `exthost-reloader/` into `~/.vscode/extensions/` (for example as
   `local.ticketdesk-terminal-bridge-0.1.0` and `local.exthost-reloader-0.1.0`) and reload the window once.
   After that, `touch ~/.claude/scripts/vscode_reload_request` reloads extensions without a window reload.

## When is it "your turn"

- `Stop`: only for interactive sessions, and only if no background task is still running (the session will wake itself
  up when it finishes) and no stop hook is continuing the turn.
- `Notification`: only permission requests. The "waiting for your input" reminder changes nothing.
- `UserPromptSubmit`: you answered, so the ▶ is removed.

## Security

The bridge binds `127.0.0.1` (first free port in 8721-8728, one per VS Code window) and rejects any request with an
`Origin` header, so web pages cannot reach it. `/type` accepts a single line of at most 4000 characters that starts
with `【`, so it never looks like a shell command; ticketdesk only sends it when the session is idle.
