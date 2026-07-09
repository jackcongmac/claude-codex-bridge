#!/usr/bin/env python3
"""Read Claude/Codex usage gauges and render a compact status-line fragment."""

import argparse
import datetime as _dt
import glob
import json
import os
import pathlib
import sys
import time

from bridge_claude_reset import claude_reset


DEFAULT_CLAUDE_USAGE = "~/.claude/bridge-usage.json"
STALE_AFTER_SECONDS = 600
SESSION_SECONDS = 5 * 3600
DEFAULT_YELLOW_AT = 60
DEFAULT_RED_AT = 80
FIELD_SEPARATOR = "  ·  "
PREFIX_WIDTH = 22
ANSI_RED_BOLD = "\033[1;31m"
ANSI_YELLOW = "\033[33m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"


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


def _round_pct(numerator, denominator):
    numerator = _to_number(numerator)
    denominator = _to_number(denominator)
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError("invalid context token ratio")
    return int(round((numerator / denominator) * 100))


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


def _text_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _model_with_effort(model, effort=None):
    model_text = _text_or_none(model)
    if model_text is None:
        return None
    effort_text = _text_or_none(effort)
    if effort_text is None:
        return model_text
    return "%s %s" % (model_text, effort_text)


def _strip_paren(text):
    return text.split(" (", 1)[0].strip()


def _claude_model(data):
    model = data.get("model")
    if not isinstance(model, dict):
        return None
    display_name = _text_or_none(model.get("display_name"))
    if display_name is None:
        return None
    effort = data.get("effort") or {}
    level = effort.get("level") if isinstance(effort, dict) else None
    return _model_with_effort(_strip_paren(display_name), level)


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
            "model": _claude_model(data),
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
    info = payload.get("info") or {}
    return {
        "primary_pct": _to_pct(primary["used_percent"]),
        "secondary_pct": _to_pct(secondary["used_percent"]),
        "primary_reset": primary["resets_at"],
        "secondary_reset": secondary["resets_at"],
        "ctx_pct": _codex_ctx_pct(info),
        "event_ts": _event_ts(event),
    }


def _codex_model_from_event(event):
    if event.get("type") != "turn_context":
        return None
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    settings = ((payload.get("collaboration_mode") or {}).get("settings") or {})
    if not isinstance(settings, dict):
        settings = {}
    model = payload.get("model") or settings.get("model")
    effort = payload.get("effort") or settings.get("reasoning_effort")
    return _model_with_effort(model, effort)


def _codex_ctx_pct(info):
    """Return current Codex context-window occupancy when rollout exposes it."""
    try:
        current = (info.get("last_token_usage") or {})["input_tokens"]
        window = info["model_context_window"]
        return _round_pct(current, window)
    except (KeyError, TypeError, ValueError):
        return None


def _read_codex_rollout(path, fs):
    newest = None
    model = None
    with fs.open_text(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                event_model = _codex_model_from_event(event)
                if event_model is not None:
                    model = event_model
                usage = _normalize_codex_event(event)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if usage is None:
                continue
            usage["model"] = model
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


def _ansi(text, code, color):
    if not color:
        return text
    return "%s%s%s" % (code, text, ANSI_RESET)


def _dash(color):
    return _ansi("—", ANSI_DIM, color)


def _pct_value(value, color=False, yellow_at=DEFAULT_YELLOW_AT, red_at=DEFAULT_RED_AT):
    if value is None:
        return _dash(color)
    text = _pct(value)
    number = float(value)
    if color and number >= red_at:
        return _ansi(text, ANSI_RED_BOLD, True)
    if color and number >= yellow_at:
        return _ansi(text, ANSI_YELLOW, True)
    return text


def _fmt_reset(reset_ts, now_ts):
    if reset_ts is None:
        return ""
    secs = int(float(reset_ts) - float(now_ts))
    if secs <= 0 or secs > SESSION_SECONDS:
        return ""
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    if hours > 0:
        duration = "%dh%dm" % (hours, minutes)
    else:
        duration = "%dm" % minutes
    return " (resets in %s)" % duration


def _missing_line(name, color=False):
    return "%s  %s" % (name, _dash(color))


def _prefix(name, usage):
    model = usage.get("model") if isinstance(usage, dict) else None
    model = _text_or_none(model)
    if model is None:
        return name
    return "%s: %s" % (name, model)


def _label(name, usage):
    prefix = _prefix(name, usage)
    if len(prefix) > PREFIX_WIDTH:
        return prefix + " "
    return prefix.ljust(PREFIX_WIDTH) + "  "


def _reading_age(usage, now_ts, timestamp_key):
    return float(now_ts) - float(usage[timestamp_key])


def _is_fresh(usage, now_ts, timestamp_key):
    return _reading_age(usage, now_ts, timestamp_key) <= STALE_AFTER_SECONDS


def _age_suffix(age):
    minutes = max(1, int(age // 60))
    if minutes < 24 * 60:
        return "  (%dm ago)" % minutes
    return "  (stale)"


def _stale_line(line, age, color=False):
    return _ansi(line + _age_suffix(age), ANSI_DIM, color)


def _format_claude(claude, now_ts, color=False, yellow_at=DEFAULT_YELLOW_AT, red_at=DEFAULT_RED_AT):
    if claude is None:
        return _missing_line("Claude", color)
    age = _reading_age(claude, now_ts, "mtime")

    def build(effective_color, reset_text):
        return "%s5h %s%s%s7d %s%sctx %s" % (
            _label("Claude", claude),
            _pct_value(claude["five_hour_pct"], effective_color, yellow_at, red_at),
            reset_text,
            FIELD_SEPARATOR,
            _pct_value(claude["seven_day_pct"], effective_color, yellow_at, red_at),
            FIELD_SEPARATOR,
            _pct_value(claude.get("ctx_pct"), effective_color, yellow_at, red_at),
        )

    if age > STALE_AFTER_SECONDS:
        return _stale_line(build(False, ""), age, color)
    return build(color, _fmt_reset(claude_reset(now_ts), now_ts))


def _codex_is_fresh(codex, now_ts):
    return _is_fresh(codex, now_ts, "event_ts")


def _format_codex(codex, now_ts, color=False, yellow_at=DEFAULT_YELLOW_AT, red_at=DEFAULT_RED_AT):
    if codex is None:
        return _missing_line("Codex", color)
    age = _reading_age(codex, now_ts, "event_ts")

    def build(effective_color, reset_text):
        return "%s5h %s%s%swk %s%sctx %s" % (
            _label("Codex", codex),
            _pct_value(codex["primary_pct"], effective_color, yellow_at, red_at),
            reset_text,
            FIELD_SEPARATOR,
            _pct_value(codex["secondary_pct"], effective_color, yellow_at, red_at),
            FIELD_SEPARATOR,
            _pct_value(codex.get("ctx_pct"), effective_color, yellow_at, red_at),
        )

    if age > STALE_AFTER_SECONDS:
        return _stale_line(build(False, ""), age, color)
    return build(color, _fmt_reset(codex.get("primary_reset"), now_ts))


def format_line(
    claude,
    codex,
    now_ts,
    color=False,
    yellow_at=DEFAULT_YELLOW_AT,
    red_at=DEFAULT_RED_AT,
):
    """Format readable Claude/Codex gauges. now_ts is explicit for deterministic tests."""
    return "%s\n%s" % (
        _format_claude(claude, now_ts, color, yellow_at, red_at),
        _format_codex(codex, now_ts, color, yellow_at, red_at),
    )


def _now_from_args(args, env):
    value = args.now_ts if args.now_ts is not None else env.get("BRIDGE_USAGE_NOW_TS")
    if value is not None:
        return float(value)
    return time.time()


def _env_number(env, name, default):
    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _color_mode(args, env):
    if args.color:
        return args.color
    if env.get("NO_COLOR") is not None:
        return "never"
    mode = env.get("BRIDGE_USAGE_COLOR") or "auto"
    mode = mode.lower()
    if mode not in {"auto", "always", "never"}:
        return "auto"
    return mode


def _should_color(args, env, stdout):
    mode = _color_mode(args, env)
    if mode == "always":
        return True
    if mode == "never":
        return False
    return stdout.isatty()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print Claude/Codex usage gauge.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print normalized JSON")
    mode.add_argument("--line", action="store_true", help="print compact line (default)")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        help="colorize human output: auto, always, or never",
    )
    parser.add_argument("--now-ts", type=float, help="override current unix time")
    args = parser.parse_args(argv)

    env = os.environ
    now_ts = _now_from_args(args, os.environ)
    claude = read_claude()
    codex = read_codex()
    if args.json:
        print(json.dumps({"claude": claude, "codex": codex, "now_ts": now_ts}, sort_keys=True))
    else:
        print(
            format_line(
                claude,
                codex,
                now_ts,
                color=_should_color(args, env, sys.stdout),
                yellow_at=_env_number(env, "BRIDGE_USAGE_YELLOW_AT", DEFAULT_YELLOW_AT),
                red_at=_env_number(env, "BRIDGE_USAGE_RED_AT", DEFAULT_RED_AT),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
