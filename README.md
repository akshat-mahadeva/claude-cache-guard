# claude-cache-guard

A tiny Claude Code hook that **warns you** (it never acts) when:

| Marker | When | Message |
|---|---|---|
| 🟢 | first reply of a session | cache warm, and when it expires |
| 🟡 | context passes ~150K tokens | run `/compact` with instructions now (cheapest while the cache is warm) |
| 🔴 | context passes ~300K tokens | switching tasks? `/clear`. Same task? `/compact` |
| 🔴 | you message after the cache went cold | this message re-reads the whole context at full price |

**Why:** the desktop app's Context panel shows how *big* the conversation is, but not whether the
prompt cache is warm or cold. Re-reading a big context after the cache expired costs the most.
cache-guard fills that gap. Warnings go to you only (`systemMessage`), never into Claude's context,
so the hook itself costs no tokens. It is local: it reads your session transcript, no network.

## Install (tested path: manual)
Needs Python 3 and Claude Code. Hooks run in the terminal and the desktop app.

1. Put the script somewhere permanent, e.g.:
   ```bash
   git clone https://github.com/akshat-mahadeva/claude-cache-guard ~/.claude/claude-cache-guard
   ```
2. Back up, then add this to `~/.claude/settings.json` (merge into an existing `"hooks"` key if you have one):
   ```bash
   cp ~/.claude/settings.json ~/.claude/settings.json.bak
   ```
   ```json
   "hooks": {
     "Stop": [{ "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/cache_guard.py", "timeout": 5 }] }],
     "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/cache_guard.py", "timeout": 5 }] }]
   }
   ```
3. Start a new session and send a message. After the reply you should see a collapsed
   "Claude Code notice" with the 🟢 line. To remove: restore the backup.

**Plugin install (untested):** the repo includes `.claude-plugin/plugin.json` and `hooks/hooks.json`
(uses `${CLAUDE_PLUGIN_ROOT}`) so it should install as a plugin, but this path has not been tested.

## Settings (environment variables, optional)
`CACHE_GUARD_COMPACT_TOKENS` (default 150000) · `CACHE_GUARD_CLEAR_TOKENS` (300000) ·
`CACHE_GUARD_TTL_SECONDS` (force the cache lifetime) · `CACHE_GUARD_QUIET=1` (print nothing)

## Status line (terminal): live countdown, limits, advice
`scripts/statusline.py` prints two colored lines, using only fields Claude Code provides:

```
cache 🟢 41:12 left · hit 83% · re-cache if cold ~45K tok
ctx ▓▓░░░░░░░░ 12% 122K · 5h 3% · wk 9% · $0.43 · → /compact now
```
Green while the cache is warm, yellow in the last 5 minutes, red when cold; the context bar and the
`→ /compact` / `→ /clear` advice follow the same thresholds as the hook. `5h` / `wk` are your plan limits
(shown only when Claude Code provides them, i.e. Pro/Max); `$` is Claude Code's own session cost estimate.
A field that is missing is simply not shown.

Add to `~/.claude/settings.json` (the `refreshInterval` makes the countdown tick while you are idle,
which the hook cannot do):
```json
"statusLine": { "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/statusline.py", "refreshInterval": 1 }
```
Optional: set `CACHE_GUARD_WRITE_PRICE_PER_MTOK` to your model's cache-write price per million tokens
(from Anthropic's pricing page) to add an estimated dollar cost of re-caching; without it only the token count shows.
`NO_COLOR=1` turns colors off. **Terminal only:** the desktop app did not show a custom status line in our test,
so desktop users get the hook messages.

## Honest limits
- It reminds you; savings happen only if you act. No savings figure is claimed.
- Context size and expiry are **estimates** from the transcript, which can lag the current turn.
- Cache lifetime (1 h or 5 min) is guessed from the transcript's usage data.
- A hook only runs on events, so it cannot warn while you sit idle; the status line's countdown covers that in the terminal.
- The notice appears as small grey text labelled "Stop says:"; its text can't be styled, hence the emoji markers.
- Checked on Claude Code desktop (macOS) for `Stop` messages. The cold-cache message
  (`UserPromptSubmit`) is tested on a transcript but not yet seen live in the desktop app.

## Test without waiting
```bash
echo '{"hook_event_name":"Stop","session_id":"t","transcript_path":"/path/to/session.jsonl"}' \
  | CACHE_GUARD_COMPACT_TOKENS=1000 python3 -I scripts/cache_guard.py
```

MIT licensed.
