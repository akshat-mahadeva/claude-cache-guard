#!/usr/bin/env python3
"""cache-guard status line (terminal). Reads Claude Code's status line JSON on stdin and prints two lines:

  cache 🟢 41:12 left · hit 83% · re-cache if cold ~45K tok        <- cache state
  ctx ▓▓░░░░░░░░ 12% 122K · 5h 3% · wk 9% · $0.43 · → /compact      <- context, plan limits, cost, advice

Everything comes from fields Claude Code provides (prompt_cache, context_window, rate_limits, cost).
No numbers are invented: a field that is missing is simply not shown.
Optional: CACHE_GUARD_WRITE_PRICE_PER_MTOK=<usd per million cache-write tokens for your model, from Anthropic's pricing page>
adds an estimated "$ to re-cache"; without it only the token count is shown.
Other env vars (same as the hook): CACHE_GUARD_COMPACT_TOKENS (150000), CACHE_GUARD_CLEAR_TOKENS (300000),
CACHE_GUARD_WARN_SECONDS (300: turn yellow when this little cache time is left), NO_COLOR=1.
Set "refreshInterval": 1 on the statusLine so the countdown ticks while idle.
"""
import json
import os
import sys
import time

COMPACT = int(os.environ.get("CACHE_GUARD_COMPACT_TOKENS", "150000"))
CLEAR = int(os.environ.get("CACHE_GUARD_CLEAR_TOKENS", "300000"))
WARN = int(os.environ.get("CACHE_GUARD_WARN_SECONDS", "300"))
USE_COLOR = "NO_COLOR" not in os.environ


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s


GREEN, YELLOW, RED, DIM = "32", "33", "31", "2"


def tok(n):
    return f"{n/1000:.0f}K" if n >= 1000 else str(int(n))


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

    # --- line 1: cache
    pc = d.get("prompt_cache")
    if not isinstance(pc, dict) or not pc.get("caching_observed", True):
        line1 = c(DIM, "cache: n/a")
    else:
        exp = pc.get("expires_at")
        left = (exp - time.time()) if isinstance(exp, (int, float)) else None
        if pc.get("warm") and left and left > 0:
            m, s = divmod(int(left), 60)
            color, icon = (YELLOW, "🟡") if left <= WARN else (GREEN, "🟢")
            parts = [c(color, f"cache {icon} {m:02d}:{s:02d} left")]
        else:
            parts = [c(RED, "cache 🔴 COLD")]
        hr = pc.get("hit_ratio")
        if isinstance(hr, (int, float)):
            parts.append(f"hit {hr*100:.0f}%")
        rt = pc.get("recache_tokens_if_cold")
        if isinstance(rt, (int, float)) and rt > 0:
            est = f"re-cache if cold ~{tok(rt)} tok"
            price = os.environ.get("CACHE_GUARD_WRITE_PRICE_PER_MTOK")
            try:
                if price:
                    est += f" (~${rt * float(price) / 1e6:.2f})"
            except ValueError:
                pass
            parts.append(est)
        cause = get(pc, "last_miss_cause", "causes")
        if isinstance(cause, list) and cause:
            parts.append(c(DIM, "last miss: " + ", ".join(map(str, cause))))
        line1 = " · ".join(parts)

    # --- line 2: context, limits, cost, advice
    cu = get(d, "context_window", "current_usage")
    ctx = None
    if isinstance(cu, dict):
        ctx = sum((cu.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    pct = get(d, "context_window", "used_percentage")
    parts2 = []
    if pct is not None or ctx is not None:
        p = int(pct or 0)
        filled = max(0, min(10, round(p / 10)))
        color = RED if (ctx or 0) >= CLEAR else YELLOW if (ctx or 0) >= COMPACT else GREEN
        bar = "▓" * filled + "░" * (10 - filled)
        parts2.append(c(color, f"ctx {bar} {p}%") + (f" {tok(ctx)}" if ctx else ""))
    for label, key in (("5h", "five_hour"), ("wk", "seven_day")):
        v = get(d, "rate_limits", key, "used_percentage")
        if isinstance(v, (int, float)):
            parts2.append(f"{label} {v:.0f}%")
    cost = get(d, "cost", "total_cost_usd")
    if isinstance(cost, (int, float)):
        parts2.append(f"${cost:.2f}")
    if ctx:
        if ctx >= CLEAR:
            parts2.append(c(RED, "→ /clear (new task) or /compact"))
        elif ctx >= COMPACT:
            parts2.append(c(YELLOW, "→ /compact now"))
    line2 = " · ".join(parts2)

    print(line1)
    if line2:
        print(line2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
