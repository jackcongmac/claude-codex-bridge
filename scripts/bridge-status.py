#!/usr/bin/env python3
"""Read-only resource and safety dashboard for claude-codex-bridge."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_common as bc  # find_project_root + collab_paths (single source of truth)
import bridge_inbox


RECENT_EVENT_COUNT = 5
DELIVERY_REVIEW_VERDICTS = {"SHIP", "FIX-FIRST", "GO", "REVISE"}
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


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


def format_inbox(project, actor):
    try:
        info = bridge_inbox.status(str(project), actor)
    except Exception as exc:
        return f"Inbox {actor}: unavailable ({exc})"
    pending = info["pending"]
    if not pending:
        return f"Inbox {actor}: clear from {info['section']}"
    latest = pending[-1]
    return (
        f"Inbox {actor}: ACTION_REQUIRED {len(pending)} pending from {info['section']} "
        f"(latest #{latest['index']} {latest['digest']})"
    )


def read_text(path):
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return "", None
    except OSError as exc:
        return "", f"cannot read {path}: {exc}"


def read_outbox_sections(board_path):
    text, error = read_text(board_path)
    if error:
        return [], error
    pattern = re.compile(r"(?ms)^##[ \t]+(.+?) Outbox[ \t]*\n(.*?)(?=^##[ \t]+|\Z)")
    return [(match.group(1).strip(), match.group(0)) for match in pattern.finditer(text)], None


def normalize_reviews(review_data):
    if not isinstance(review_data, dict):
        return []
    reviews = []
    for review in review_data.get("reviews", []):
        if not isinstance(review, dict):
            continue
        verdict = str(review.get("verdict") or "").upper()
        sha = str(review.get("sha") or "").strip().lower()
        if verdict in DELIVERY_REVIEW_VERDICTS and sha:
            reviews.append({**review, "verdict": verdict, "sha": sha})
    return reviews


def extract_sha_tokens(body):
    return [match.group(0).lower() for match in SHA_RE.finditer(body or "")]


def review_matches_token(review_sha, token):
    return review_sha.startswith(token) or token.startswith(review_sha)


def reviews_for_tokens(tokens, reviews):
    if not tokens:
        return []
    matched = []
    for review in reviews:
        review_sha = review["sha"]
        if any(review_matches_token(review_sha, token) for token in tokens):
            matched.append(review)
    return matched


def state_key_peer(key):
    if "<-" not in key:
        return None
    return key.split("<-", 1)[1].strip()


def ack_records_for_actor(ack_state, actor):
    records = []
    if not isinstance(ack_state, dict):
        return records
    for key, record in ack_state.items():
        if key.startswith("esc:") or not isinstance(record, dict):
            continue
        if record.get("peer") == actor or record.get("section") == f"{actor} Outbox":
            records.append(record)
            continue
        if state_key_peer(key) == actor:
            records.append(record)
    return records


def ack_covers_entry(entries, ack, entry):
    pending = bridge_inbox.pending_entries(entries, ack)
    return entry["digest"] not in {pending_entry["digest"] for pending_entry in pending}


def covering_ack(entries, ack_records, entry):
    for ack in ack_records:
        if ack_covers_entry(entries, ack, entry):
            return ack
    return None


def overdue_digests_for_actor(ack_state, actor):
    digests = set()
    if not isinstance(ack_state, dict):
        return digests
    for key, esc in ack_state.items():
        if not key.startswith("esc:") or not isinstance(esc, dict):
            continue
        if state_key_peer(key.removeprefix("esc:")) == actor and esc.get("last_escalated_digest"):
            digests.add(esc["last_escalated_digest"])
    return digests


def shipped_sha(root, sha, cache):
    if sha in cache:
        return cache[sha]
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        cache[sha] = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        cache[sha] = False
    return cache[sha]


def classify_delivery_item(root, actor, entries, entry, ack_state, reviews, shipped_cache):
    ack = covering_ack(entries, ack_records_for_actor(ack_state, actor), entry)
    tokens = extract_sha_tokens(entry["body"])
    matched_reviews = reviews_for_tokens(tokens, reviews)
    candidate_shas = sorted(set(tokens + [review["sha"] for review in matched_reviews]))
    shipped = [sha for sha in candidate_shas if shipped_sha(root, sha, shipped_cache)]
    overdue = entry["digest"] in overdue_digests_for_actor(ack_state, actor) and ack is None

    if shipped:
        state = "shipped"
    elif overdue:
        state = "overdue"
    elif matched_reviews:
        state = "reviewed"
    elif ack is not None:
        state = "claimed"
    else:
        state = "open"

    return {
        "state": state,
        "actor": actor,
        "index": entry["index"],
        "digest": entry["digest"],
        "timestamp": entry["timestamp"],
        "summary": bridge_inbox.summarize_body(entry["body"], width=96),
        "sha": (shipped or candidate_shas or [None])[0],
        "review": matched_reviews[-1] if matched_reviews else None,
        "ack": ack,
        "overdue": overdue,
    }


def format_delivery_item(item):
    detail = []
    if item["sha"]:
        detail.append(item["sha"][:12])
    review = item["review"]
    if review:
        detail.append(str(review.get("verdict")))
    ack = item["ack"]
    if ack and ack.get("status"):
        detail.append(f"receipt={ack['status']}")
    if item["overdue"]:
        detail.append("sla=active")
    if not detail:
        detail.append("no-sha")
    return (
        f"  {item['state']:<8} {item['actor']} #{item['index']} {item['digest']} "
        f"{' '.join(detail)} {item['summary']}"
    )


def build_delivery_dashboard(project):
    root = bc.find_project_root(str(project)) if project else bc.find_project_root()
    paths = bc.collab_paths(root)
    sections, board_error = read_outbox_sections(Path(paths["board"]))
    if board_error:
        return "", board_error, 2

    ack_data, ack_error = read_json(Path(paths["inbox_ack"]))
    if ack_error:
        return "", ack_error, 2
    review_data, review_error = read_json(Path(paths["reviews"]))
    if review_error:
        return "", review_error, 2

    ack_state = ack_data if isinstance(ack_data, dict) else {}
    reviews = normalize_reviews(review_data)
    shipped_cache = {}
    items = []
    for actor, section_text in sections:
        entries = bridge_inbox.parse_entries(section_text)
        for entry in entries:
            items.append(
                classify_delivery_item(root, actor, entries, entry, ack_state, reviews, shipped_cache)
            )

    lines = [
        "Delivery Dashboard",
        f"Project: {root}  (.collab: {paths['dir']})",
        f"Work items: {len(items)}",
    ]
    if items:
        lines.append("State     Work item")
        lines.extend(format_delivery_item(item) for item in items)
    else:
        lines.append("State     Work item")
        lines.append("  none")
    return "\n".join(lines) + "\n", "", 0


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
        f"Project: {root}  (.collab: {paths['dir']})",
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
            format_inbox(root, "Claude"),
            format_inbox(root, "Codex"),
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


def print_once(project, delivery=False):
    output, error, code = build_delivery_dashboard(project) if delivery else build_dashboard(project)
    if output:
        sys.stdout.write(output)
    if error:
        sys.stderr.write(error + "\n")
    return code


def watch(project, interval, delivery=False):
    try:
        while True:
            code = print_once(project, delivery=delivery)
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
        "--delivery",
        action="store_true",
        help="Show the read-only delivery lifecycle dashboard instead of the resource dashboard.",
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
        return watch(args.project, args.interval, delivery=args.delivery)
    return print_once(args.project, delivery=args.delivery)


if __name__ == "__main__":
    raise SystemExit(main())
