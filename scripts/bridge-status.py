#!/usr/bin/env python3
"""Read-only resource and safety dashboard for claude-codex-bridge."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_common as bc  # find_project_root + collab_paths (single source of truth)


STATE_FILE = "collaboration_state.json"
SIGNAL_FILE = "collaboration_signal.json"
LOG_FILE = "collaboration_auto.log"
RECENT_EVENT_COUNT = 5


def read_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except FileNotFoundError:
        return None, None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON in {path}: {exc}"
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"


def read_recent_events(path, limit=RECENT_EVENT_COUNT):
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        return [], f"cannot read {path}: {exc}"
    return lines[-limit:], None


def format_value(value):
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def format_mapping(mapping):
    if not isinstance(mapping, dict) or not mapping:
        return "none"
    return ", ".join(f"{key}={format_value(mapping[key])}" for key in sorted(mapping))


def format_list(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return format_value(value)


def format_resource_profile(actor, profile):
    if not isinstance(profile, dict):
        return f"  {actor}: {format_value(profile)}"
    parts = []
    tier = profile.get("tier")
    if tier is not None:
        parts.append(f"tier={format_value(tier)}")
    if "best_for" in profile:
        parts.append(f"best_for={format_list(profile.get('best_for'))}")
    if "avoid" in profile:
        parts.append(f"avoid={format_list(profile.get('avoid'))}")
    if not parts:
        parts.append(format_value(profile))
    return f"  {actor}: " + "; ".join(parts)


def format_money(value):
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return format_value(value)


def format_turn(state):
    turn = state.get("turn")
    max_turns = state.get("max_turns")
    if turn is None and max_turns is None:
        return "unknown"
    return f"{format_value(turn)}/{format_value(max_turns)}"


def format_cost(state):
    spent = format_money(state.get("cost_so_far_usd"))
    maximum = format_money(state.get("max_cost_usd"))
    return f"{spent} / {maximum}"


def format_signal(signal):
    if not signal:
        return "unavailable"

    update_id = signal.get("update_id")
    updated_by = format_value(signal.get("updated_by"))
    updated_at = signal.get("updated_at")
    summary = signal.get("summary")

    prefix = f"#{format_value(update_id)} by {updated_by}"
    if updated_at:
        prefix += f" at {updated_at}"
    if summary:
        return f"{prefix} - {summary}"
    return prefix


def build_dashboard(project):
    # Resolve the project root (any cwd depth) and read from <root>/.collab/ via
    # the single source of truth, so we never report a stale legacy flat board.
    root = bc.find_project_root(str(project)) if project else bc.find_project_root()
    paths = bc.collab_paths(root)
    state, state_error = read_json(Path(paths["state"]))
    signal, signal_error = read_json(Path(paths["signal"]))
    events, log_error = read_recent_events(Path(paths["log"]))

    if state_error:
        return "", state_error, 2
    if signal_error:
        return "", signal_error, 2
    if log_error:
        return "", log_error, 2

    state = state or {}
    lines = [
        "Bridge Status",
        f"Project: {project}",
        f"Status: {format_value(state.get('status', 'unavailable'))}",
        f"Turn: {format_turn(state)}",
        f"Next actor: {format_value(state.get('next_actor'))}",
        f"Roles: {format_mapping(state.get('roles'))}",
    ]

    resource_profiles = state.get("resource_profiles")
    if resource_profiles:
        lines.append("Resource profiles:")
        if isinstance(resource_profiles, dict):
            lines.extend(
                format_resource_profile(actor, resource_profiles[actor])
                for actor in sorted(resource_profiles)
            )
        else:
            lines.append(f"  {format_value(resource_profiles)}")

    lines.extend(
        [
            f"Cost: {format_cost(state)}",
            f"Last writer: {format_value(state.get('last_writer'))}",
            f"Last signal: {format_signal(signal)}",
        ]
    )

    if events:
        lines.append("Recent events:")
        lines.extend(f"  {event}" for event in events)
    else:
        lines.append("Recent events: none")

    if state.get("status") == "awaiting_human":
        failure = state.get("failure") or state.get("failure_reason")
        lines.append(f"Failure: {format_value(failure)}")

    return "\n".join(lines) + "\n", "", 0


def print_once(project):
    output, error, code = build_dashboard(project)
    if output:
        sys.stdout.write(output)
    if error:
        sys.stderr.write(error + "\n")
    return code


def watch(project, interval):
    try:
        while True:
            code = print_once(project)
            sys.stdout.flush()
            if code != 0:
                return code
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Show a read-only claude-codex-bridge resource/safety dashboard."
    )
    parser.add_argument(
        "--project",
        default=os.getcwd(),
        help="Project directory containing collaboration_state.json.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll and reprint the dashboard until interrupted.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds for --watch.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.interval <= 0:
        sys.stderr.write("--interval must be greater than zero\n")
        return 2
    if args.watch:
        return watch(args.project, args.interval)
    return print_once(args.project)


if __name__ == "__main__":
    raise SystemExit(main())
