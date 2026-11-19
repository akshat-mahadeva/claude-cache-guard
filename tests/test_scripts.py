"""Run with:  python3 -m unittest discover -s tests -v   (no dependencies)"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "scripts", "cache_guard.py")
STATUS = os.path.join(ROOT, "scripts", "statusline.py")


def iso(seconds_ago):
    return datetime.fromtimestamp(time.time() - seconds_ago, timezone.utc).isoformat().replace("+00:00", "Z")


def assistant(seconds_ago, ctx=50000, ttl_key="ephemeral_1h_input_tokens", cache_write=1000):
    cc = {ttl_key: cache_write} if ttl_key else {}
    return json.dumps({"timestamp": iso(seconds_ago), "message": {"role": "assistant", "usage": {
        "input_tokens": 5, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": cache_write,
        "cache_creation": cc}}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.n = 0

    def transcript(self, *lines):
        self.n += 1
        p = os.path.join(self.tmp.name, f"t{self.n}.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(lines) + "\n")
        return p

    def hook(self, event, transcript, session="s1", **env):
        e = dict(os.environ, CACHE_GUARD_STATE_DIR=self.state, CACHE_GUARD_WAIT_MS="0")
        e.update(env)
        r = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(
            {"hook_event_name": event, "session_id": session, "transcript_path": transcript}),
            capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else ""

    def status(self, payload, raw=None, **env):
        e = dict(os.environ, NO_COLOR="1", **env)
        r = subprocess.run([sys.executable, "-I", STATUS], input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class HookTests(Base):
    def test_warm_notice_once_per_session(self):
        t = self.transcript(assistant(10))
        self.assertIn("Cache warm", self.hook("Stop", t))
        self.assertEqual(self.hook("Stop", t), "")

    def test_stop_counts_from_now_when_transcript_lags(self):
        # the transcript still shows the previous reply (14 min ago); the reply that just finished refreshed the cache
        t = self.transcript(assistant(840))
        msg = self.hook("Stop", t)
        self.assertIn("about 60m", msg)
        self.assertNotIn("46m", msg)

    def test_cold_notice_on_prompt_after_idle(self):
        t = self.transcript(assistant(4000))
        self.assertIn("probably COLD", self.hook("UserPromptSubmit", t))

    def test_no_cold_notice_when_still_warm(self):
        t = self.transcript(assistant(60))
        self.assertEqual(self.hook("UserPromptSubmit", t), "")

    def test_five_minute_cache_goes_cold_after_six_minutes(self):
        t = self.transcript(assistant(400, ttl_key="ephemeral_5m_input_tokens"))
        self.assertIn("probably COLD", self.hook("UserPromptSubmit", t))

    def test_first_reply_waits_for_transcript_to_catch_up(self):
        t = self.transcript("")  # no assistant message yet
        e = dict(os.environ, CACHE_GUARD_STATE_DIR=self.state, CACHE_GUARD_WAIT_MS="3000")
        p = subprocess.Popen([sys.executable, "-I", HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=e)
        p.stdin.write(json.dumps({"hook_event_name": "Stop", "session_id": "new", "transcript_path": t}))
        p.stdin.close()
        time.sleep(0.6)
        with open(t, "a") as f:
            f.write(assistant(1) + "\n")  # the reply lands in the transcript after the hook started
        out = p.stdout.read()
        p.wait()
        self.assertIn("Cache warm", out)

    def test_five_minute_cache_notice_says_5m(self):
        t = self.transcript(assistant(10, ttl_key="ephemeral_5m_input_tokens"))
        self.assertIn("about 5m", self.hook("Stop", t))

    def test_unknown_ttl_is_never_assumed(self):
        t = self.transcript(assistant(100000, ttl_key=None, cache_write=0))
        self.assertEqual(self.hook("UserPromptSubmit", t), "")
        self.assertEqual(self.hook("Stop", t), "")

    def test_ttl_override(self):
        t = self.transcript(assistant(100, ttl_key=None, cache_write=0))
        self.assertIn("probably COLD", self.hook("UserPromptSubmit", t, CACHE_GUARD_TTL_SECONDS="10"))

    def test_threshold_levels_fire_once_and_rearm(self):
        big = self.transcript(assistant(10, ctx=160000))
        self.assertIn("compact", self.hook("Stop", big).lower())
        self.assertEqual(self.hook("Stop", big), "")  # no repeat, and no green line after it
        huge = self.transcript(assistant(10, ctx=320000))
        self.assertIn("/clear", self.hook("Stop", huge))
        small = self.transcript(assistant(10, ctx=20000))
        self.hook("Stop", small)  # re-arms
        self.assertIn("compact", self.hook("Stop", big).lower())

    def test_malformed_input_is_silent(self):
        t = self.transcript("not json", "[]", json.dumps({"message": "x"}),
                            json.dumps({"timestamp": 5, "message": {"role": "assistant", "usage": {"input_tokens": "a"}}}))
        self.assertEqual(self.hook("Stop", t), "")
        r = subprocess.run([sys.executable, "-I", HOOK], input="garbage", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_missing_transcript(self):
        self.assertEqual(self.hook("Stop", os.path.join(self.tmp.name, "nope.jsonl")), "")

    def test_session_id_cannot_escape_state_dir(self):
        t = self.transcript(assistant(10))
        self.hook("Stop", t, session="../../evil")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil.json")))
        self.assertTrue(any(n.endswith(".json") for n in os.listdir(self.state)))
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), "0o700")

    def test_symlinked_state_file_is_not_followed(self):
        t = self.transcript(assistant(10))
        os.makedirs(self.state, mode=0o700)
        victim = os.path.join(self.tmp.name, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, os.path.join(self.state, "s1.json"))
        self.hook("Stop", t)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")

    def test_large_transcript_uses_tail(self):
        filler = [json.dumps({"x": "y" * 1000})] * 1500  # about 1.5 MB before the real message
        t = self.transcript(assistant(5000), *filler, assistant(10))
        self.assertIn("Cache warm", self.hook("Stop", t))


class StatusTests(Base):
    def payload(self, **pc):
        base = {"warm": True, "caching_observed": True, "expires_at": time.time() + 3000, "hit_ratio": 0.8,
                "recache_tokens_if_cold": 45000}
        base.update(pc)
        return {"prompt_cache": base, "context_window": {"used_percentage": 12, "context_window_size": 1000000,
                "current_usage": {"input_tokens": 5, "cache_read_input_tokens": 120000, "cache_creation_input_tokens": 0}},
                "rate_limits": {"five_hour": {"used_percentage": 3}}, "cost": {"total_cost_usd": 0.4}}

    def test_normal(self):
        out = self.status(self.payload())
        self.assertIn("cache 🟢", out)
        self.assertIn("5h 3%", out)
        self.assertIn("$0.40", out)

    def test_missing_warm_is_unknown_not_cold(self):
        p = self.payload()
        del p["prompt_cache"]["warm"], p["prompt_cache"]["expires_at"]
        out = self.status(p)
        self.assertIn("cache: ?", out)
        self.assertNotIn("COLD", out)

    def test_expired_is_cold(self):
        self.assertIn("COLD", self.status(self.payload(expires_at=time.time() - 5)))

    def test_missing_percentage_is_not_zero(self):
        p = self.payload()
        del p["context_window"]["used_percentage"]
        p["context_window"].pop("context_window_size")
        out = self.status(p)
        self.assertNotIn(" 0%", out)
        self.assertIn("ctx 120K", out)

    def test_percentage_computed_from_tokens_when_size_known(self):
        p = self.payload()
        del p["context_window"]["used_percentage"]
        self.assertIn("12%", self.status(p))

    def test_bad_numbers_do_not_crash(self):
        p = self.payload(hit_ratio="x", recache_tokens_if_cold=None, expires_at="soon")
        p["rate_limits"] = {"five_hour": {"used_percentage": "lots"}}
        p["cost"] = {"total_cost_usd": True}
        out = self.status(p)
        self.assertIn("cache", out)
        self.assertNotIn("5h", out)
        self.assertNotIn("$", out)

    def test_control_characters_are_stripped(self):
        out = self.status(self.payload(last_miss_cause={"causes": ["tools_changed\x1b[2J\x07"]}))
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)

    def test_small_context_window_lowers_thresholds(self):
        p = self.payload()
        p["context_window"].update(context_window_size=200000)  # 75% = 150K; usage is 120K -> no advice
        self.assertNotIn("/compact", self.status(p))
        p["context_window"]["current_usage"]["cache_read_input_tokens"] = 160000
        self.assertIn("/compact", self.status(p))

    def test_price_only_when_set(self):
        self.assertNotIn("(~$", self.status(self.payload()))
        self.assertIn("(~$0.28)", self.status(self.payload(), CACHE_GUARD_WRITE_PRICE_PER_MTOK="6.25"))

    def test_garbage_input(self):
        self.assertIn("n/a", self.status(None, raw="garbage"))
        self.assertIn("n/a", self.status(None, raw="[]"))


if __name__ == "__main__":
    unittest.main()
