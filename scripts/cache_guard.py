#!/usr/bin/env python3
"""claude-cache-guard: tell the user when the prompt cache is about to go cold and when to /compact or /clear.

Reads the hook JSON from stdin (needs `transcript_path`), looks at the last assistant message's
token usage and timestamp, and prints a warning for the USER only ({"systemMessage": ...}).
It never changes anything and never adds text to Claude's context.

Events:
  Stop              -> after each reply: cache expiry time, "compact now" nudge when context is large
  UserPromptSubmit  -> if you were idle past the cache lifetime: "this message re-reads everything"
  --status          -> one line for a status line (reads the same JSON on stdin)

Settings (environment variables, all optional):
  CACHE_GUARD_COMPACT_TOKENS  context size that triggers the compact nudge (default 150000)
  CACHE_GUARD_CLEAR_TOKENS    context size that triggers the clear suggestion (default 300000)
  CACHE_GUARD_TTL_SECONDS     force the cache lifetime (default: guessed from the transcript)
  CACHE_GUARD_QUIET=1         print nothing

Numbers are estimates from the transcript, which can lag the current turn.
"""
import json
import os
import sys
import time
from datetime import datetime

COMPACT = int(os.environ.get("CACHE_GUARD_COMPACT_TOKENS", "150000"))
CLEAR = int(os.environ.get("CACHE_GUARD_CLEAR_TOKENS", "300000"))


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def last_usage(path):
    """Return (timestamp, context_tokens, ttl_seconds) of the last assistant message, or None."""
    found = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message")
                if not isinstance(m, dict) or m.get("role") != "assistant":
                    continue
                u = m.get("usage")
                ts = parse_ts(d.get("timestamp", ""))
                if not isinstance(u, dict) or ts is None:
                    continue
                ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                    + (u.get("cache_creation_input_tokens") or 0)
                cc = u.get("cache_creation") or {}
                ttl = 3600 if (cc.get("ephemeral_1h_input_tokens") or 0) > 0 else \
                    300 if (cc.get("ephemeral_5m_input_tokens") or 0) > 0 else None
                found = (ts, ctx, ttl, found[2] if found and ttl is None else ttl)
    except OSError:
        return None
    if not found:
        return None
    ts, ctx, ttl, prev = found
    return ts, ctx, ttl or prev


def fmt_tokens(n):
    return f"{n/1000:.0f}K" if n >= 1000 else str(n)


def fmt_dur(s):
    s = max(0, int(s))
    return f"{s//60}m" if s >= 60 else f"{s}s"


def state_path(session):
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), f"cache-guard-{session or 'x'}.json")


def load_state(session):
    try:
        return json.load(open(state_path(session)))
    except Exception:
        return {}


def save_state(session, st):
    try:
        json.dump(st, open(state_path(session), "w"))
    except OSError:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if os.environ.get("CACHE_GUARD_QUIET") == "1":
        return 0
    event = data.get("hook_event_name", "")
    path = data.get("transcript_path")
    info = last_usage(path) if path else None
    if not info:
        if "--status" in sys.argv:
            print("cache: n/a")
        return 0
    ts, ctx, ttl = info
    ttl = int(os.environ.get("CACHE_GUARD_TTL_SECONDS", ttl or 3600))
    now = time.time()
    left = ts + ttl - now
    session = data.get("session_id", "")

    if "--status" in sys.argv:
        state = f"warm {fmt_dur(left)} left" if left > 0 else "cold"
        print(f"cache: {state} | ctx {fmt_tokens(ctx)}")
        return 0

    st = load_state(session)
    msgs = []

    if event == "UserPromptSubmit":
        if left <= 0:
            idle = now - ts
            msgs.append(f"🔴 Cache is COLD (idle {fmt_dur(idle)}): this message re-reads about {fmt_tokens(ctx)} tokens "
                        f"at full price. If the old task is done, /clear before the next one.")
    elif event == "Stop":
        level = 2 if ctx >= CLEAR else 1 if ctx >= COMPACT else 0
        if level > st.get("level", 0):
            if level == 2:
                msgs.append(f"🔴 Context ~{fmt_tokens(ctx)}. Switching tasks? Run /clear. Same task? /compact with instructions now.")
            else:
                msgs.append(f"🟡 Context ~{fmt_tokens(ctx)}. Run /compact with instructions now (cheapest while the cache is warm).")
            st["level"] = level
        elif level < st.get("level", 0):
            st["level"] = level  # compacted or cleared: re-arm
        if not msgs and not st.get("expiry_shown"):
            msgs.append(f"🟢 Cache warm for {fmt_dur(left)} (until {datetime.fromtimestamp(ts + ttl).strftime('%H:%M')}). "
                        f"A break longer than that means the next message rebuilds the cache.")
            st["expiry_shown"] = True  # once per session, not after every reply
        save_state(session, st)

    if msgs:
        print(json.dumps({"systemMessage": "cache-guard " + " ".join(msgs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
