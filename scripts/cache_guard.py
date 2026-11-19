#!/usr/bin/env python3
"""claude-cache-guard: tell the user when the prompt cache is probably cold and when to /compact or /clear.

Reads the hook JSON from stdin (needs `transcript_path`), looks at the last assistant message's
token usage and timestamp, and prints a warning for the USER only ({"systemMessage": ...}).
It never changes anything and never adds text to Claude's context.

Events:
  Stop              -> after a reply: "compact now" / "clear" nudges when context is large; cache expiry note once
  UserPromptSubmit  -> if you were idle past the cache lifetime: "cache is probably cold" notice
  --status          -> one line for a status line (reads the same JSON on stdin)

Settings (environment variables, all optional):
  CACHE_GUARD_COMPACT_TOKENS  context size that triggers the compact nudge (default 150000)
  CACHE_GUARD_CLEAR_TOKENS    context size that triggers the clear suggestion (default 300000)
  CACHE_GUARD_TTL_SECONDS     force the cache lifetime (default: read from the transcript's usage data)
  CACHE_GUARD_STATE_DIR       where the small per-session state files live
                              (default ~/.claude/claude-cache-guard-state, private to you)
  CACHE_GUARD_WAIT_MS         on Stop, how long to wait for the transcript to contain the reply (default 1500)
  CACHE_GUARD_QUIET=1         print nothing

Cache lifetime is read from the transcript (1 h or 5 min cache writes). If it cannot be read, the lifetime is
UNKNOWN and all time-based messages are skipped; it is never assumed. Numbers are estimates: the transcript
can lag the current turn and the timestamp is only a proxy for the cache's real expiry.
"""
import json
import os
import re
import sys
import time
from datetime import datetime


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


COMPACT = env_int("CACHE_GUARD_COMPACT_TOKENS", 150000)
CLEAR = env_int("CACHE_GUARD_CLEAR_TOKENS", 300000)
STATE_DIR = os.environ.get("CACHE_GUARD_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "claude-cache-guard-state")
TAIL_BYTES = 1_000_000


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else 0.0


def scan(lines):
    """Return (timestamp, context_tokens, ttl_seconds_or_None) of the last assistant message, or None."""
    found = None
    ttl_seen = None
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        m = d.get("message")
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        u = m.get("usage")
        ts = parse_ts(d.get("timestamp"))
        if not isinstance(u, dict) or ts is None:
            continue
        ctx = int(num(u.get("input_tokens")) + num(u.get("cache_read_input_tokens"))
                  + num(u.get("cache_creation_input_tokens")))
        cc = u.get("cache_creation")
        cc = cc if isinstance(cc, dict) else {}
        if num(cc.get("ephemeral_1h_input_tokens")) > 0:
            ttl_seen = 3600
        elif num(cc.get("ephemeral_5m_input_tokens")) > 0:
            ttl_seen = 300
        found = (ts, ctx, ttl_seen)  # carries the last TTL that was actually observed
    return found


def last_usage(path):
    """Read only the tail of big transcripts; fall back to the whole file if the tail has no usable message."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - TAIL_BYTES)
            f.seek(start)
            data = f.read()
            lines = data.decode("utf-8", "replace").splitlines()
            if start > 0 and lines:
                lines = lines[1:]  # first line is probably cut in half
            found = scan(lines)
            if found is None and start > 0:
                f.seek(0)
                found = scan(f.read().decode("utf-8", "replace").splitlines())
            return found
    except OSError:
        return None


def fmt_tokens(n):
    return f"{n/1000:.0f}K" if n >= 1000 else str(n)


def fmt_dur(s):
    s = max(0, int(s))
    return f"{s//60}m" if s >= 60 else f"{s}s"


# ---- per-session state: private folder, sanitized names, no symlink following, old files pruned
def state_file(session):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session))[:64] or "x"
    return os.path.join(STATE_DIR, safe + ".json")


def load_state(session):
    try:
        with open(state_file(session), "r") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(session, st):
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        cutoff = time.time() - 7 * 86400
        for name in os.listdir(STATE_DIR):
            p = os.path.join(STATE_DIR, name)
            if name.endswith(".json") and os.path.isfile(p) and not os.path.islink(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(state_file(session), flags, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(st, f)
    except OSError:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict) or os.environ.get("CACHE_GUARD_QUIET") == "1":
        return 0
    event = data.get("hook_event_name", "")
    path = data.get("transcript_path")
    info = last_usage(path) if isinstance(path, str) and path else None
    if not info and event == "Stop" and isinstance(path, str) and path:
        # first reply of a new session: the transcript is written asynchronously and may not hold it yet
        deadline = time.time() + env_int("CACHE_GUARD_WAIT_MS", 1500) / 1000
        while not info and time.time() < deadline:
            time.sleep(0.25)
            info = last_usage(path)
    if not info:
        if "--status" in sys.argv:
            print("cache: n/a")
        return 0
    ts, ctx, ttl = info
    override = os.environ.get("CACHE_GUARD_TTL_SECONDS")
    if override:
        try:
            ttl = int(override)
        except ValueError:
            pass
    now = time.time()
    left = (ts + ttl - now) if ttl is not None else None  # None = lifetime unknown
    session = data.get("session_id", "")

    if "--status" in sys.argv:
        if left is None:
            print(f"cache: ? | ctx {fmt_tokens(ctx)}")
        else:
            state = f"warm {fmt_dur(left)} left" if left > 0 else "cold"
            print(f"cache: {state} | ctx {fmt_tokens(ctx)}")
        return 0

    st = load_state(session)
    msgs = []

    if event == "UserPromptSubmit":
        if left is not None and left <= 0:
            msgs.append(f"🔴 Cache is probably COLD (idle {fmt_dur(now - ts)}, estimate): this message re-reads about "
                        f"{fmt_tokens(ctx)} tokens without the cache discount. If the old task is done, /clear first.")
    elif event == "Stop":
        level = 2 if ctx >= CLEAR else 1 if ctx >= COMPACT else 0
        if level > st.get("level", 0):
            if level == 2:
                msgs.append(f"🔴 Context ~{fmt_tokens(ctx)}. Switching tasks? Run /clear. Same task? /compact with instructions now.")
            else:
                msgs.append(f"🟡 Context ~{fmt_tokens(ctx)}. Run /compact with instructions now (cheapest while the cache is warm).")
            st["level"] = level
            st["expiry_shown"] = True  # a threshold notice replaces the expiry note; no green line right after it
        elif level < st.get("level", 0):
            st["level"] = level  # compacted or cleared: re-arm
        # A reply just finished, so its API call just refreshed the cache. The transcript may not contain that
        # reply yet (it lags), so count from now, not from the older timestamp it still shows.
        if not msgs and not st.get("expiry_shown") and ttl is not None and ttl > 0:
            msgs.append(f"🟢 Cache warm for about {fmt_dur(ttl)} (until {datetime.fromtimestamp(now + ttl).strftime('%H:%M')}). "
                        f"A break longer than that means the next message rebuilds it.")
            st["expiry_shown"] = True  # once per session, not after every reply
        save_state(session, st)

    if msgs:
        print(json.dumps({"systemMessage": "cache-guard " + " ".join(msgs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
