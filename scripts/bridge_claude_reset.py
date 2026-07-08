#!/usr/bin/env python3
"""Compute Claude's active 5-hour reset from local transcript activity."""

import datetime as _dt
import glob
import json
import pathlib


DEFAULT_PROJECTS_DIR = "~/.claude/projects"
DEFAULT_CACHE_PATH = "~/.claude/bridge-claude-reset.json"


def _floor_to_hour(ts):
    return float(int(float(ts)) // 3600 * 3600)


def identify_reset(timestamps, now_ts, session_hours=5):
    session = float(session_hours) * 3600
    block_start = None
    last = None
    for t in sorted(float(value) for value in timestamps):
        if block_start is None:
            block_start = _floor_to_hour(t)
        elif (t - block_start) > session or (t - last) > session:
            block_start = _floor_to_hour(t)
        last = t

    if block_start is None:
        return None

    end = block_start + session
    now = float(now_ts)
    if (now - last) < session and now < end:
        return end
    return None


def _parse_iso_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _activity_timestamps(projects_dir, now_ts, lookback_h=6):
    root = pathlib.Path(projects_dir).expanduser()
    cutoff = float(now_ts) - float(lookback_h) * 3600
    timestamps = []
    for name in glob.glob(str(root / "**" / "*.jsonl"), recursive=True):
        path = pathlib.Path(name)
        try:
            if path.stat().st_mtime < cutoff:
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if '"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = entry.get("message") if isinstance(entry, dict) else None
                    if not isinstance(msg, dict) or not msg.get("usage"):
                        continue
                    ts = _parse_iso_timestamp(entry.get("timestamp"))
                    if ts is not None:
                        timestamps.append(ts)
        except (OSError, UnicodeDecodeError):
            continue
    return timestamps


def _read_cache(cache_path, now_ts, ttl):
    try:
        path = pathlib.Path(cache_path).expanduser()
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        computed_at = float(payload["computed_at"])
        if float(now_ts) - computed_at >= float(ttl):
            return None, False
        reset_ts = payload.get("reset_ts")
        if reset_ts is None:
            return None, True
        reset_ts = float(reset_ts)
        if reset_ts > float(now_ts):
            return reset_ts, True
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    return None, False


def _write_cache(cache_path, now_ts, reset_ts):
    try:
        path = pathlib.Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"reset_ts": reset_ts, "computed_at": float(now_ts)}
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
    except OSError:
        pass


def claude_reset(
    now_ts,
    projects_dir=DEFAULT_PROJECTS_DIR,
    cache_path=DEFAULT_CACHE_PATH,
    ttl=120,
):
    try:
        cached, ok = _read_cache(cache_path, now_ts, ttl)
        if ok:
            return cached
        reset_ts = identify_reset(_activity_timestamps(projects_dir, now_ts), now_ts)
        _write_cache(cache_path, now_ts, reset_ts)
        return reset_ts
    except Exception:
        return None
