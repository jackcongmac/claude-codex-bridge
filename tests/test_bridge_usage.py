import json
import os
import pathlib
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bridge_usage  # noqa: E402


ANSI_ESC = "\033["


class RecordingFS:
    def __init__(self):
        self.local = bridge_usage.LocalFS()
        self.opened = []

    def exists(self, path):
        return self.local.exists(path)

    def stat(self, path):
        return self.local.stat(path)

    def glob(self, pattern):
        return self.local.glob(pattern)

    def open_text(self, path):
        self.opened.append(str(path))
        return self.local.open_text(path)


class BridgeUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmpdir.name)
        self.addCleanup(self.tmpdir.cleanup)

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_rollout(self, path, events, mtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    def claude_payload(self, five=3, seven=8, ctx=21):
        return {
            "rate_limits": {
                "five_hour": {"used_percentage": five, "resets_at": 1000},
                "seven_day": {"used_percentage": seven, "resets_at": 2000},
            },
            "context_window": {
                "used_percentage": ctx,
                "remaining_percentage": 100 - ctx,
                "context_window_size": 200000,
            },
            "model": {"id": "claude-opus", "display_name": "Claude Opus"},
        }

    def codex_event(self, ts=1234, primary=14.2, secondary=2.4, info=None):
        if info is None:
            info = {
                "model_context_window": 200000,
                "last_token_usage": {
                    "input_tokens": 46000,
                    "total_tokens": 47000,
                },
                "total_token_usage": {
                    "input_tokens": 300000,
                    "total_tokens": 304000,
                },
            }
        return {
            "type": "event_msg",
            "timestamp": ts,
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": primary,
                        "window_minutes": 300,
                        "resets_at": 3000,
                    },
                    "secondary": {
                        "used_percent": secondary,
                        "window_minutes": 10080,
                        "resets_at": 4000,
                    },
                },
                "info": info,
            },
        }

    def sample_claude_usage(self, five=3, seven=8, ctx=21):
        return {
            "five_hour_pct": five,
            "seven_day_pct": seven,
            "five_hour_reset": 1000,
            "seven_day_reset": 2000,
            "ctx_pct": ctx,
            "mtime": 990,
        }

    def sample_codex_usage(self, primary=14.2, secondary=2.4, ctx=23, event_ts=1000):
        return {
            "primary_pct": primary,
            "secondary_pct": secondary,
            "primary_reset": 3000,
            "secondary_reset": 4000,
            "ctx_pct": ctx,
            "event_ts": event_ts,
        }

    def run_main(self, argv, env=None, claude=None, codex=None):
        old_read_claude = bridge_usage.read_claude
        old_read_codex = bridge_usage.read_codex
        old_env = os.environ.copy()
        os.environ.pop("BRIDGE_USAGE_COLOR", None)
        os.environ.pop("NO_COLOR", None)
        if env:
            os.environ.update(env)
        bridge_usage.read_claude = lambda: claude
        bridge_usage.read_codex = lambda: codex
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                rc = bridge_usage.main(argv)
        finally:
            bridge_usage.read_claude = old_read_claude
            bridge_usage.read_codex = old_read_codex
            os.environ.clear()
            os.environ.update(old_env)
        return rc, stdout.getvalue()

    def test_read_claude_parses_confirmed_schema_and_mtime(self):
        path = self.tmp / "bridge-usage.json"
        self.write_json(path, self.claude_payload(five=5, seven=9, ctx=13))
        os.utime(path, (1111, 1111))

        usage = bridge_usage.read_claude(path)

        self.assertEqual(
            usage,
            {
                "five_hour_pct": 5,
                "seven_day_pct": 9,
                "five_hour_reset": 1000,
                "seven_day_reset": 2000,
                "ctx_pct": 13,
                "mtime": 1111,
            },
        )

    def test_read_claude_missing_file_returns_none(self):
        self.assertIsNone(bridge_usage.read_claude(self.tmp / "missing.json"))

    def test_read_codex_picks_newest_token_count_event_in_newest_rollout(self):
        home = self.tmp / "codex"
        older = home / "sessions" / "2026" / "07" / "03" / "rollout-old.jsonl"
        newest = home / "sessions" / "2026" / "07" / "04" / "rollout-new.jsonl"
        self.write_rollout(older, [self.codex_event(ts=100, primary=1)], mtime=100)
        self.write_rollout(
            newest,
            [
                self.codex_event(ts=200, primary=14.1, secondary=2.1),
                {"type": "event_msg", "timestamp": 300, "payload": {"type": "other"}},
                self.codex_event(ts=250, primary=16.7, secondary=3.2),
            ],
            mtime=300,
        )
        (home / "logs_2.sqlite").write_text("stale sqlite is ignored", encoding="utf-8")

        usage = bridge_usage.read_codex(home)

        self.assertEqual(usage["primary_pct"], 16.7)
        self.assertEqual(usage["secondary_pct"], 3.2)
        self.assertEqual(usage["primary_reset"], 3000)
        self.assertEqual(usage["secondary_reset"], 4000)
        self.assertEqual(usage["ctx_pct"], 23)
        self.assertEqual(usage["event_ts"], 250)

    def test_read_codex_computes_ctx_pct_from_last_input_tokens_and_window(self):
        home = self.tmp / "codex"
        rollout = home / "sessions" / "2026" / "07" / "04" / "rollout-ctx.jsonl"
        self.write_rollout(
            rollout,
            [
                self.codex_event(
                    ts=500,
                    info={
                        "model_context_window": 200000,
                        "last_token_usage": {
                            "input_tokens": 45678,
                            "total_tokens": 47000,
                        },
                        "total_token_usage": {
                            "input_tokens": 345678,
                            "total_tokens": 350000,
                        },
                    },
                )
            ],
            mtime=500,
        )

        usage = bridge_usage.read_codex(home)

        self.assertEqual(usage["ctx_pct"], 23)

    def test_read_codex_cumulative_only_ctx_is_unknown_not_over_100(self):
        home = self.tmp / "codex"
        rollout = home / "sessions" / "2026" / "07" / "04" / "rollout-cumulative-only.jsonl"
        self.write_rollout(
            rollout,
            [
                self.codex_event(
                    ts=500,
                    info={
                        "model_context_window": 200000,
                        "total_token_usage": {
                            "input_tokens": 345678,
                            "total_tokens": 350000,
                        },
                    },
                )
            ],
            mtime=500,
        )

        usage = bridge_usage.read_codex(home)

        self.assertIsNone(usage["ctx_pct"])

    def test_read_codex_picks_newest_event_across_rollouts_with_mtime_boundary(self):
        home = self.tmp / "codex"
        latest_mtime = home / "sessions" / "2026" / "07" / "04" / "rollout-latest-mtime.jsonl"
        newer_event = home / "sessions" / "2026" / "07" / "04" / "rollout-newer-event.jsonl"
        older_bound = home / "sessions" / "2026" / "07" / "03" / "rollout-older-bound.jsonl"
        self.write_rollout(latest_mtime, [self.codex_event(ts=100, primary=1.0)], mtime=1000)
        self.write_rollout(newer_event, [self.codex_event(ts=850, primary=44.4)], mtime=900)
        self.write_rollout(older_bound, [self.codex_event(ts=650, primary=2.0)], mtime=700)
        fs = RecordingFS()

        usage = bridge_usage.read_codex(home, fs=fs)

        self.assertEqual(usage["primary_pct"], 44.4)
        self.assertEqual(usage["event_ts"], 850)
        self.assertNotIn(str(older_bound), fs.opened)

    def test_read_codex_falls_back_when_newest_rollout_has_no_token_count(self):
        home = self.tmp / "codex"
        empty = home / "sessions" / "2026" / "07" / "04" / "rollout-empty.jsonl"
        fallback = home / "sessions" / "2026" / "07" / "03" / "rollout-fallback.jsonl"
        self.write_rollout(
            empty,
            [{"type": "event_msg", "timestamp": 900, "payload": {"type": "other"}}],
            mtime=900,
        )
        self.write_rollout(fallback, [self.codex_event(ts=800, primary=22.2)], mtime=800)

        usage = bridge_usage.read_codex(home)

        self.assertEqual(usage["primary_pct"], 22.2)
        self.assertEqual(usage["event_ts"], 800)

    def test_read_codex_no_token_count_returns_none(self):
        home = self.tmp / "codex"
        rollout = home / "sessions" / "2026" / "07" / "04" / "rollout-env.jsonl"
        self.write_rollout(
            rollout,
            [{"type": "event_msg", "timestamp": 100, "payload": {"type": "other"}}],
            mtime=100,
        )

        self.assertIsNone(bridge_usage.read_codex(home))

    def test_read_codex_honors_codex_home_environment(self):
        home = self.tmp / "env-codex"
        rollout = home / "sessions" / "2026" / "07" / "04" / "rollout-env.jsonl"
        self.write_rollout(rollout, [self.codex_event(ts=500, primary=31.4)], mtime=500)
        old = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(home)
        if old is not None:
            self.addCleanup(lambda: os.environ.__setitem__("CODEX_HOME", old))
        else:
            self.addCleanup(lambda: os.environ.pop("CODEX_HOME", None))

        usage = bridge_usage.read_codex()

        self.assertEqual(usage["primary_pct"], 31.4)
        self.assertEqual(usage["event_ts"], 500)

    def test_format_line_shows_both_sides_when_present(self):
        line = bridge_usage.format_line(
            self.sample_claude_usage(ctx=21),
            self.sample_codex_usage(ctx=23),
            now_ts=1005,
        )

        self.assertIn("CC 5h 3% · 7d 8% · ctx 21%", line)
        self.assertIn("Cx 5h 14% · wk 2% · ctx 23%", line)
        self.assertNotIn("—", line)

    def test_format_line_ctx_none_renders_dash_for_both_sides(self):
        line = bridge_usage.format_line(
            self.sample_claude_usage(ctx=None),
            self.sample_codex_usage(ctx=None),
            now_ts=1005,
        )

        self.assertIn("CC 5h 3% · 7d 8% · ctx —", line)
        self.assertIn("Cx 5h 14% · wk 2% · ctx —", line)

    def test_format_line_marks_stale_codex_with_age_not_zero_percent(self):
        line = bridge_usage.format_line(
            None,
            {
                "primary_pct": 55.0,
                "secondary_pct": 6.0,
                "primary_reset": 3000,
                "secondary_reset": 4000,
                "event_ts": 1000,
            },
            now_ts=1720,
        )

        self.assertIn("CC —", line)
        self.assertIn("Cx 5h 55% · wk 6% · ctx —", line)
        self.assertIn("(12m ago)", line)
        self.assertNotIn("Cx 5h 0%", line)

    def test_format_line_missing_side_shows_dash(self):
        line = bridge_usage.format_line(
            self.sample_claude_usage(ctx=21),
            None,
            now_ts=1000,
        )

        self.assertEqual(line, "CC 5h 3% · 7d 8% · ctx 21%   ·   Cx —")

    def test_field_name_normalization_for_both_sides(self):
        claude_path = self.tmp / "claude.json"
        codex_home = self.tmp / "codex"
        rollout = codex_home / "sessions" / "2026" / "07" / "04" / "rollout-normalize.jsonl"
        self.write_json(claude_path, self.claude_payload(five=41, seven=12, ctx=7))
        self.write_rollout(rollout, [self.codex_event(ts=600, primary=33.8, secondary=4.1)], mtime=600)

        claude = bridge_usage.read_claude(claude_path)
        codex = bridge_usage.read_codex(codex_home)

        self.assertEqual(claude["five_hour_pct"], 41)
        self.assertEqual(claude["seven_day_pct"], 12)
        self.assertEqual(codex["primary_pct"], 33.8)
        self.assertEqual(codex["secondary_pct"], 4.1)

    def test_format_line_colorizes_percentages_dash_and_stale_suffix(self):
        line = bridge_usage.format_line(
            self.sample_claude_usage(five=80, seven=60, ctx=59),
            self.sample_codex_usage(primary=79, secondary=12, ctx=None, event_ts=1000),
            now_ts=1720,
            color=True,
        )

        self.assertIn("\033[1;31m80%\033[0m", line)
        self.assertIn("\033[33m60%\033[0m", line)
        self.assertIn("ctx 59%", line)
        self.assertIn("\033[33m79%\033[0m", line)
        self.assertIn("wk 12%", line)
        self.assertIn("ctx \033[2m—\033[0m", line)
        self.assertIn("\033[2m(12m ago)\033[0m", line)

    def test_format_line_color_never_has_no_ansi(self):
        line = bridge_usage.format_line(
            self.sample_claude_usage(five=90, seven=70, ctx=None),
            self.sample_codex_usage(primary=90, secondary=70, ctx=None, event_ts=1000),
            now_ts=1720,
            color=False,
        )

        self.assertNotIn(ANSI_ESC, line)
        self.assertIn("ctx —", line)
        self.assertIn("(12m ago)", line)

    def test_main_color_always_forces_ansi_when_stdout_is_not_tty(self):
        rc, output = self.run_main(
            ["--color", "always", "--now-ts", "1720"],
            claude=self.sample_claude_usage(five=80),
            codex=self.sample_codex_usage(ctx=None, event_ts=1000),
        )

        self.assertEqual(rc, 0)
        self.assertIn("\033[1;31m80%\033[0m", output)
        self.assertIn("\033[2m—\033[0m", output)

    def test_main_color_never_and_no_color_env_emit_no_ansi(self):
        for argv, env in (
            (["--color", "never", "--now-ts", "1720"], {}),
            (["--now-ts", "1720"], {"NO_COLOR": "1"}),
        ):
            with self.subTest(argv=argv, env=env):
                rc, output = self.run_main(
                    argv,
                    env=env,
                    claude=self.sample_claude_usage(five=80),
                    codex=self.sample_codex_usage(ctx=None, event_ts=1000),
                )

                self.assertEqual(rc, 0)
                self.assertNotIn(ANSI_ESC, output)

    def test_main_json_is_never_colored_and_includes_codex_ctx_pct(self):
        rc, output = self.run_main(
            ["--json", "--color", "always", "--now-ts", "1720"],
            claude=self.sample_claude_usage(five=80),
            codex=self.sample_codex_usage(ctx=23, event_ts=1000),
        )

        self.assertEqual(rc, 0)
        self.assertNotIn(ANSI_ESC, output)
        payload = json.loads(output)
        self.assertEqual(payload["codex"]["ctx_pct"], 23)


if __name__ == "__main__":
    unittest.main()
