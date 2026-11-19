# claude-cache-guard

A small Claude Code add-on (Python 3, standard library only) that **warns you, and never acts**, about the
prompt cache and context size. Two parts:

- **Hook** (`scripts/cache_guard.py`): a short notice after a reply or before your next message.
- **Status line** (`scripts/statusline.py`, terminal): a live cache countdown, context bar, plan limits and advice.

| Marker | When | Message |
|---|---|---|
| 🟢 | first reply of a session (if no other notice) | cache warm; counted from the reply that just finished |
| 🟡 | context passes ~150K tokens | run `/compact` with instructions now (cheapest while the cache is warm) |
| 🔴 | context passes ~300K tokens | switching tasks? `/clear`. Same task? `/compact` |
| 🔴 | you message after the cache probably went cold | this message re-reads about N tokens without the cache discount (estimate) |

**Why:** the desktop app's Context panel shows how *big* the conversation is, not whether the prompt cache is
still warm. When it has expired, the next message has to rebuild it
([how caching and its lifetime work](https://code.claude.com/docs/en/prompt-caching)).

**Privacy and safety:** local only, no network. It reads your session transcript and writes small flag files to
`~/.claude/claude-cache-guard-state/` (private folder, old files removed after 7 days). It emits only a
`systemMessage`, which the [hooks docs](https://code.claude.com/docs/en/hooks) describe as a warning shown to the user;
it does not use `additionalContext`, the field that adds text to Claude's context.

## Install (tested path: manual)
Needs Python 3 and Claude Code.

1. Put the scripts somewhere permanent:
   ```bash
   git clone https://github.com/akshat-mahadeva/claude-cache-guard ~/.claude/claude-cache-guard
   ```
2. Add the entries below to `~/.claude/settings.json`. Keep your other keys; if you already have a `"hooks"` key,
   add the two events inside it. Before:
   ```json
   { "theme": "dark" }
   ```
   After:
   ```json
   {
     "theme": "dark",
     "hooks": {
       "Stop": [{ "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/cache_guard.py", "timeout": 5 }] }],
       "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/cache_guard.py", "timeout": 5 }] }]
     },
     "statusLine": { "type": "command", "command": "python3 -I ~/.claude/claude-cache-guard/scripts/statusline.py", "refreshInterval": 1 }
   }
   ```
   (`statusLine` is optional and terminal-only; see below. `refreshInterval` makes the countdown tick while you are idle,
   as documented in the [status line docs](https://code.claude.com/docs/en/statusline).)
3. Start a **new** session and send a message. Hooks and the status line load at session start.

**Remove:** delete the `"hooks"` entries and the `"statusLine"` key you added from `settings.json` (don't restore an old
backup over it: that would also undo any other settings you changed since). Then delete `~/.claude/claude-cache-guard`.

**Plugin install (untested):** the repo includes `.claude-plugin/plugin.json` and `hooks/hooks.json`
(uses `${CLAUDE_PLUGIN_ROOT}`), so it should install as a plugin, but this path has not been tested.

## Status line (terminal)
```
cache 🟢 41:12 left · hit 83% · re-cache if cold ~45K tok
ctx ▓▓░░░░░░░░ 12% 122K · 5h 3% · wk 9% · $0.43 · → /compact now
```
Green while warm, yellow in the last 5 minutes, red when cold. Everything comes from fields Claude Code provides
(`prompt_cache`, `context_window`, `rate_limits`, `cost`; see the
[status line docs](https://code.claude.com/docs/en/statusline)). A missing or malformed field is not shown; if Claude Code
does not say whether the cache is warm, it shows `cache: ?` instead of guessing. `5h` / `wk` appear only where Claude Code
provides them (the docs list Pro, Max and Ultra plans); `$` is Claude Code's own session cost estimate.
The context advice thresholds are capped at 75% (compact) and 90% (clear) of the context window when Claude Code reports
its size, so small windows warn earlier.

Optional: `CACHE_GUARD_WRITE_PRICE_PER_MTOK` (your model's cache-write price per million tokens, from Anthropic's
pricing page) adds an estimated dollar cost of re-caching; without it only the token count is shown. `NO_COLOR=1` turns colors off.

## Settings (environment variables, optional)
`CACHE_GUARD_COMPACT_TOKENS` (150000) · `CACHE_GUARD_CLEAR_TOKENS` (300000) · `CACHE_GUARD_TTL_SECONDS` (force the cache
lifetime) · `CACHE_GUARD_WARN_SECONDS` (status line: yellow below this, default 300) · `CACHE_GUARD_STATE_DIR` ·
`CACHE_GUARD_QUIET=1` (print nothing). The hook's token thresholds are absolute, so lower them for small context windows.

## Honest limits
- It reminds you; savings happen only if you act. No savings figure is claimed.
- Context size and expiry are **estimates** from the transcript, which can lag the current turn; the last message's
  timestamp is only a proxy for the cache's real expiry.
- After a reply the hook assumes that reply just refreshed the cache (the transcript lags by one reply, so it can't be
  used for that moment); the status line has the exact expiry from Claude Code. Context size in the hook's 🟡/🔴 notices
  can also lag by one reply.
- The cache lifetime (1 h or 5 min) is read from the transcript's usage data. If it can't be read, the hook treats it as
  **unknown** and skips time-based notices; it never assumes one hour.
- A hook only runs on events, so it can't warn while you sit idle; the status line's countdown covers that in the terminal.
- **Where each part shows (macOS, Claude Code v2.1.328):** the status line shows in the **terminal only** (the desktop app
  showed no custom status line). The hook notice (a grey "Stop says: cache-guard ..." line under the reply) showed in the
  **desktop app** and, in a second terminal test, in the **terminal**. In one earlier terminal run the hook executed but no
  notice appeared; cause unknown, so treat the terminal notice as likely, not guaranteed.
- The cold-cache notice (`UserPromptSubmit`) is covered by tests but has not been seen live in the desktop app.
- The notice is small grey text labelled "Stop says:"; its text can't be styled, hence the emoji markers.

## Tests
```bash
python3 -m unittest discover -s tests -v
```
Covers expired and unknown cache lifetime, repeated and re-armed thresholds, malformed transcripts, large transcripts,
unsafe session IDs and symlinked state files, and missing, null or malformed status line fields.

MIT licensed.
