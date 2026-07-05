#!/usr/bin/env python3
"""Read Claude/Codex usage gauges and render a compact status-line fragment."""

import argparse
import datetime as _dt
import glob
import json
import os
import pathlib
import time


DEFAULT_CLAUDE_USAGE = "~/.claude/bridge-usage.json"
STALE_AFTER_SECONDS = 600


class LocalFS:
    def exists(self, path):
        return pathlib.Path(path).expanduser().exists()

    def stat(self, path):
        return pathlib.Path(path).expanduser().stat()

    def glob(self, pattern):
        return glob.glob(str(pathlib.Path(pattern).expanduser()))

    def open_text(self, path):
        return open(pathlib.Path(path).expanduser(), "r", encoding="utf-8")


def _fs(fs):
    return fs if fs is not None else LocalFS()


def _to_number(value):
    if value is None:
        raise ValueError("missing number")
    return float(value)


def _to_pct(value):
    number = _to_number(value)
    return int(number) if number.is_integer() else number


def _event_ts(event):
    for key in ("timestamp", "ts", "time"):
        value = event.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                text = value[:-1] + "+00:00" if value.endswith("Z") else value
                return _dt.datetime.fromisoformat(text).timestamp()
    raise ValueError("missing event timestamp")


def read_claude(path=DEFAULT_CLAUDE_USAGE, fs=None):
    """Return normalized Claude usage, or None when the capture is absent/unreadable."""
    fs = _fs(fs)
    try:
        if not fs.exists(path):
            return None
        with fs.open_text(path) as handle:
            data = json.load(handle)
        five = data["rate_limits"]["five_hour"]
        seven = data["rate_limits"]["seven_day"]
        context = data["context_window"]
        return {
            "five_hour_pct": _to_pct(five["used_percentage"]),
            "seven_day_pct": _to_pct(seven["used_percentage"]),
            "five_hour_reset": five["resets_at"],
            "seven_day_reset": seven["resets_at"],
            "ctx_pct": _to_pct(context["used_percentage"]),
            "mtime": fs.stat(path).st_mtime,
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _rollout_files(home, fs):
    pattern = pathlib.Path(home).expanduser() / "sessions" / "*" / "*" / "*" / "rollout-*.jsonl"
    files = []
    for path in fs.glob(pattern):
        try:
            files.append((fs.stat(path).st_mtime, path))
        except OSError:
            continue
    return sorted(files, key=lambda item: item[0], reverse=True)


def _normalize_codex_event(event):
    payload = event.get("payload") or {}
    if event.get("type") != "event_msg" or payload.get("type") != "token_count":
        return None
    rate_limits = payload["rate_limits"]
    primary = rate_limits["primary"]
    secondary = rate_limits["secondary"]
    return {
        "primary_pct": _to_pct(primary["used_percent"]),
        "secondary_pct": _to_pct(secondary["used_percent"]),
        "primary_reset": primary["resets_at"],
        "secondary_reset": secondary["resets_at"],
        "event_ts": _event_ts(event),
    }


def _read_codex_rollout(path, fs):
    newest = None
    with fs.open_text(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                usage = _normalize_codex_event(event)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if usage is None:
                continue
            if newest is None or usage["event_ts"] > newest["event_ts"]:
                newest = usage
    return newest


def read_codex(home=None, fs=None, env=None):
    """Return normalized Codex usage from rollout token_count events, or None."""
    fs = _fs(fs)
    env = os.environ if env is None else env
    if home is None:
        home = env.get("CODEX_HOME") or "~/.codex"

    best = None
    files = _rollout_files(home, fs)
    for index, (_mtime, path) in enumerate(files):
        try:
            usage = _read_codex_rollout(path, fs)
        except OSError:
            continue
        if usage is not None:
            if best is None or usage["event_ts"] > best["event_ts"]:
                best = usage
        if best is not None:
            next_mtime = files[index + 1][0] if index + 1 < len(files) else None
            if next_mtime is None or best["event_ts"] >= next_mtime:
                return best
    return best


def _pct(value):
    return "%d%%" % int(round(float(value)))


def _format_claude(claude):
    if claude is None:
        return "CC —"
    # Quota only (5h/weekly). Context% is owned by ccstatusline's own widget, so
    # we deliberately omit ctx here to avoid a duplicate/second Claude number.
    # ctx_pct is still retained in read_claude()/--json for later use.
    return "CC 5h %s · 7d %s" % (
        _pct(claude["five_hour_pct"]),
        _pct(claude["seven_day_pct"]),
    )


def _codex_age_suffix(codex, now_ts):
    age = float(now_ts) - float(codex["event_ts"])
    if age <= STALE_AFTER_SECONDS:
        return ""
    minutes = max(1, int(age // 60))
    if minutes < 24 * 60:
        return " (%dm ago)" % minutes
    return " (stale)"


def _format_codex(codex, now_ts):
    if codex is None:
        return "Cx —"
    return "Cx 5h %s · wk %s%s" % (
        _pct(codex["primary_pct"]),
        _pct(codex["secondary_pct"]),
        _codex_age_suffix(codex, now_ts),
    )


def format_line(claude, codex, now_ts):
    """Format a compact combined gauge. now_ts is explicit for deterministic tests."""
    return "%s   ·   %s" % (_format_claude(claude), _format_codex(codex, now_ts))


def _now_from_args(args, env):
    value = args.now_ts if args.now_ts is not None else env.get("BRIDGE_USAGE_NOW_TS")
    if value is not None:
        return float(value)
    return time.time()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print Claude/Codex usage gauge.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print normalized JSON")
    mode.add_argument("--line", action="store_true", help="print compact line (default)")
    parser.add_argument("--now-ts", type=float, help="override current unix time")
    args = parser.parse_args(argv)

    now_ts = _now_from_args(args, os.environ)
    claude = read_claude()
    codex = read_codex()
    if args.json:
        print(json.dumps({"claude": claude, "codex": codex, "now_ts": now_ts}, sort_keys=True))
    else:
        print(format_line(claude, codex, now_ts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
