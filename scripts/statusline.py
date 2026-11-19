#!/usr/bin/env python3
"""claude-cache-guard status line (terminal). Reads Claude Code's status line JSON on stdin and prints two lines:

  cache 🟢 41:12 left · hit 83% · re-cache if cold ~45K tok        <- cache state
  ctx ▓▓░░░░░░░░ 12% 122K · 5h 3% · wk 9% · $0.43 · → /compact      <- context, plan limits, cost, advice

Everything comes from fields Claude Code provides (prompt_cache, context_window, rate_limits, cost); see
https://code.claude.com/docs/en/statusline. A field that is missing or malformed is not shown, and the cache
state is shown as "cache: ?" when Claude Code does not say whether it is warm. Nothing is guessed.
Optional: CACHE_GUARD_WRITE_PRICE_PER_MTOK=<usd per million cache-write tokens for your model, from Anthropic's pricing page>
adds an estimated "$ to re-cache"; without it only the token count is shown.
Env vars: CACHE_GUARD_COMPACT_TOKENS (150000), CACHE_GUARD_CLEAR_TOKENS (300000) (both are capped at 75% / 90% of
the context window when Claude Code reports its size), CACHE_GUARD_WARN_SECONDS (300: turn yellow when this little
cache time is left), NO_COLOR=1. Set "refreshInterval": 1 on the statusLine so the countdown ticks while idle.
"""
import json
import os
import re
import sys
import time


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


COMPACT = env_int("CACHE_GUARD_COMPACT_TOKENS", 150000)
CLEAR = env_int("CACHE_GUARD_CLEAR_TOKENS", 300000)
WARN = env_int("CACHE_GUARD_WARN_SECONDS", 300)
USE_COLOR = "NO_COLOR" not in os.environ
GREEN, YELLOW, RED, DIM = "32", "33", "31", "2"


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s


def tok(n):
    return f"{n/1000:.0f}K" if n >= 1000 else str(int(n))


def num(x):
    """A real number or None (rejects strings, bools, NaN)."""
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def clean(s):
    """Letters, digits and a few punctuation marks only: no terminal control characters from JSON text."""
    return re.sub(r"[^A-Za-z0-9_ .\-]", "", str(s))[:40]


def get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        print("cache: n/a")
        return 0
    if not isinstance(d, dict):
        print("cache: n/a")
        return 0

    # --- line 1: cache
    pc = d.get("prompt_cache")
    if not isinstance(pc, dict) or pc.get("caching_observed") is False:
        line1 = c(DIM, "cache: n/a")
    else:
        exp = num(pc.get("expires_at"))
        left = (exp - time.time()) if exp is not None else None
        warm = pc.get("warm")
        if warm is True and left is not None and left > 0:
            m, s = divmod(int(left), 60)
            color, icon = (YELLOW, "🟡") if left <= WARN else (GREEN, "🟢")
            parts = [c(color, f"cache {icon} {m:02d}:{s:02d} left")]
        elif warm is False or (left is not None and left <= 0):
            parts = [c(RED, "cache 🔴 COLD")]
        else:
            parts = [c(DIM, "cache: ?")]  # Claude Code did not say: do not guess
        hr = num(pc.get("hit_ratio"))
        if hr is not None:
            parts.append(f"hit {hr*100:.0f}%")
        rt = num(pc.get("recache_tokens_if_cold"))
        if rt is not None and rt > 0:
            est = f"re-cache if cold ~{tok(rt)} tok"
            try:
                price = float(os.environ.get("CACHE_GUARD_WRITE_PRICE_PER_MTOK", ""))
                est += f" (~${rt * price / 1e6:.2f})"
            except ValueError:
                pass
            parts.append(est)
        cause = get(pc, "last_miss_cause", "causes")
        if isinstance(cause, list) and cause:
            parts.append(c(DIM, "last miss: " + ", ".join(clean(x) for x in cause[:3])))
        line1 = " · ".join(parts)

    # --- line 2: context, limits, cost, advice
    cu = get(d, "context_window", "current_usage")
    ctx = None
    if isinstance(cu, dict):
        vals = [num(cu.get(k)) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
        if any(v is not None for v in vals):
            ctx = sum(v for v in vals if v is not None)
    size = num(get(d, "context_window", "context_window_size"))
    pct = num(get(d, "context_window", "used_percentage"))
    if pct is None and ctx is not None and size:
        pct = ctx / size * 100
    compact_at, clear_at = COMPACT, CLEAR
    if size:
        compact_at, clear_at = min(compact_at, 0.75 * size), min(clear_at, 0.9 * size)
    parts2 = []
    if pct is not None:
        filled = max(0, min(10, round(pct / 10)))
        color = RED if (ctx or 0) >= clear_at else YELLOW if (ctx or 0) >= compact_at else GREEN
        parts2.append(c(color, f"ctx {'▓' * filled}{'░' * (10 - filled)} {pct:.0f}%") + (f" {tok(ctx)}" if ctx else ""))
    elif ctx:
        parts2.append(f"ctx {tok(ctx)}")
    for label, key in (("5h", "five_hour"), ("wk", "seven_day")):
        v = num(get(d, "rate_limits", key, "used_percentage"))
        if v is not None:
            parts2.append(f"{label} {v:.0f}%")
    cost = num(get(d, "cost", "total_cost_usd"))
    if cost is not None:
        parts2.append(f"${cost:.2f}")
    if ctx:
        if ctx >= clear_at:
            parts2.append(c(RED, "→ /clear (new task) or /compact"))
        elif ctx >= compact_at:
            parts2.append(c(YELLOW, "→ /compact now"))

    print(line1)
    if parts2:
        print(" · ".join(parts2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
