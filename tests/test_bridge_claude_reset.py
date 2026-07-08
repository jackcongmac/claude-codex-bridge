import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _reset_module(testcase):
    try:
        import bridge_claude_reset  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        testcase.fail("bridge_claude_reset module is missing: %s" % exc)
    return bridge_claude_reset


def _ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class BridgeClaudeResetTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.tmpdir.name)
        self.addCleanup(self.tmpdir.cleanup)

    def write_jsonl(self, path, entries, mtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for entry in entries:
            if isinstance(entry, str):
                lines.append(entry)
            else:
                lines.append(json.dumps(entry))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def usage_entry(self, timestamp):
        return {
            "timestamp": timestamp,
            "message": {"usage": {"input_tokens": 1, "output_tokens": 1}},
        }

    def test_floor_to_hour_truncates_to_utc_hour(self):
        reset = _reset_module(self)
        ts = _ts("2026-07-07T20:05:42Z")

        self.assertEqual(reset._floor_to_hour(ts), _ts("2026-07-07T20:00:00Z"))

    def test_identify_reset_single_recent_block_uses_floor_of_first_plus_five_hours(self):
        reset = _reset_module(self)
        timestamps = [_ts("2026-07-07T20:05:00Z"), _ts("2026-07-07T21:10:00Z")]

        result = reset.identify_reset(timestamps, now_ts=_ts("2026-07-07T22:00:00Z"))

        self.assertEqual(result, _ts("2026-07-08T01:00:00Z"))

    def test_identify_reset_gap_over_session_uses_latest_block(self):
        reset = _reset_module(self)
        timestamps = [_ts("2026-07-07T10:05:00Z"), _ts("2026-07-07T16:01:00Z")]

        result = reset.identify_reset(timestamps, now_ts=_ts("2026-07-07T17:00:00Z"))

        self.assertEqual(result, _ts("2026-07-07T21:00:00Z"))

    def test_identify_reset_inactive_when_last_activity_over_session_ago(self):
        reset = _reset_module(self)
        timestamps = [_ts("2026-07-07T10:05:00Z")]

        result = reset.identify_reset(timestamps, now_ts=_ts("2026-07-07T15:06:00Z"))

        self.assertIsNone(result)

    def test_identify_reset_empty_returns_none(self):
        reset = _reset_module(self)

        self.assertIsNone(reset.identify_reset([], now_ts=_ts("2026-07-07T12:00:00Z")))

    def test_identify_reset_inactive_when_now_is_past_block_end(self):
        reset = _reset_module(self)
        timestamps = [_ts("2026-07-07T20:05:00Z"), _ts("2026-07-08T00:59:00Z")]

        result = reset.identify_reset(timestamps, now_ts=_ts("2026-07-08T01:00:01Z"))

        self.assertIsNone(result)

    def test_activity_timestamps_reads_recent_usage_entries_and_skips_bad_inputs(self):
        reset = _reset_module(self)
        now_ts = _ts("2026-07-08T02:00:00Z")
        recent = self.tmp / "proj" / "conversation.jsonl"
        old = self.tmp / "proj" / "old.jsonl"
        valid_ts = "2026-07-08T00:15:00Z"
        other_valid_ts = "2026-07-08T01:30:00+00:00"
        self.write_jsonl(
            recent,
            [
                self.usage_entry(valid_ts),
                {"timestamp": "2026-07-08T00:20:00Z", "message": {"content": "no usage"}},
                "{malformed",
                self.usage_entry(other_valid_ts),
                {"timestamp": "not-a-date", "message": {"usage": {"input_tokens": 2}}},
            ],
            mtime=now_ts - 60,
        )
        self.write_jsonl(
            old,
            [self.usage_entry("2026-07-07T18:30:00Z")],
            mtime=now_ts - 7 * 3600,
        )

        result = reset._activity_timestamps(self.tmp, now_ts=now_ts, lookback_h=6)

        self.assertEqual(sorted(result), [_ts(valid_ts), _ts(other_valid_ts)])

    def test_claude_reset_reuses_cache_within_ttl_and_recomputes_after_ttl(self):
        reset = _reset_module(self)
        projects = self.tmp / "projects"
        cache_path = self.tmp / "cache.json"
        transcript = projects / "proj" / "conversation.jsonl"
        first_now = _ts("2026-07-08T02:00:00Z")
        self.write_jsonl(
            transcript,
            [self.usage_entry("2026-07-08T00:05:00Z")],
            mtime=first_now,
        )

        first = reset.claude_reset(first_now, projects_dir=projects, cache_path=cache_path, ttl=120)
        self.write_jsonl(
            transcript,
            [self.usage_entry("2026-07-08T01:05:00Z")],
            mtime=first_now + 30,
        )
        cached = reset.claude_reset(first_now + 30, projects_dir=projects, cache_path=cache_path, ttl=120)
        recomputed = reset.claude_reset(first_now + 121, projects_dir=projects, cache_path=cache_path, ttl=120)

        self.assertEqual(first, _ts("2026-07-08T05:00:00Z"))
        self.assertEqual(cached, first)
        self.assertEqual(recomputed, _ts("2026-07-08T06:00:00Z"))


if __name__ == "__main__":
    unittest.main()
