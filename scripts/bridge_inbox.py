#!/usr/bin/env python3
"""Machine-checkable peer inbox for persistent board collaboration.

The collaboration board already proves a peer window is live, but liveness is not
the same as task ownership. This module treats the peer's Outbox as my Inbox and
records explicit ACK/CLAIM/DECLINE/DONE state outside the Outbox so replies do not
create new work items.
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_common as bc  # noqa: E402


STATUSES = ("ACK", "CLAIM", "DECLINE", "DONE")


def peer_for(self_name, peer_name=None):
    if peer_name:
        return peer_name
    if self_name in bc.OTHER:
        return bc.OTHER[self_name]
    raise ValueError("--peer is required when --self is not Claude or Codex")


def inbox_section(self_name, peer_name=None):
    return "%s Outbox" % peer_for(self_name, peer_name)


def _entry_digest(timestamp, body):
    raw = ("%s\n%s" % (timestamp.strip(), body.strip())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def parse_entries(section_text):
    found = []
    pattern = re.compile(r"(?ms)^###[ \t]+([^\n]+)\n\n(.*?)(?=^###[ \t]+|\Z)")
    for match in pattern.finditer(section_text or ""):
        body = match.group(2).strip()
        if not body:
            continue
        found.append(
            {
                "timestamp": match.group(1).strip(),
                "body": body,
                "digest": _entry_digest(match.group(1), body),
            }
        )
    # Board appends insert new entries directly below the section header, so file
    # order is newest-first. Normalize to chronological order so "latest" is [-1].
    entries = list(reversed(found))
    for idx, entry in enumerate(entries, start=1):
        entry["index"] = idx
    return entries


def load_entries(project, self_name, peer_name=None):
    paths = bc.collab_paths(project)
    section = inbox_section(self_name, peer_name)
    return parse_entries(bc.read_section(paths["board"], section))


def load_state(paths):
    state = bc.read_json(paths["inbox_ack"], default={}) or {}
    return state if isinstance(state, dict) else {}


def state_key(self_name, peer_name):
    return "%s<- %s" % (self_name, peer_name)


def get_ack(state, self_name, peer_name):
    return state.get(state_key(self_name, peer_name), {})


def pending_entries(entries, ack):
    if not entries:
        return []
    last_digest = ack.get("last_digest")
    if last_digest:
        for idx, entry in enumerate(entries):
            if entry["digest"] == last_digest:
                return entries[idx + 1 :]
    last_index = ack.get("last_index")
    if isinstance(last_index, int) and 0 <= last_index <= len(entries):
        return entries[last_index:]
    return entries


def summarize_body(body, width=120):
    one_line = " ".join((body or "").split())
    if len(one_line) <= width:
        return one_line
    return one_line[: width - 3] + "..."


def status(project, self_name, peer_name=None):
    peer_name = peer_for(self_name, peer_name)
    paths = bc.collab_paths(project)
    entries = load_entries(project, self_name, peer_name)
    ack = get_ack(load_state(paths), self_name, peer_name)
    pending = pending_entries(entries, ack)
    return {
        "self": self_name,
        "peer": peer_name,
        "section": "%s Outbox" % peer_name,
        "entries": entries,
        "ack": ack,
        "pending": pending,
    }


def format_pending(info, limit=5):
    pending = info["pending"]
    section = info["section"]
    if not pending:
        ack = info["ack"] or {}
        suffix = ""
        if ack.get("last_digest"):
            suffix = " last_%s=%s" % (str(ack.get("status", "ACK")).lower(), ack["last_digest"])
        return "Inbox: CLEAR for %s.%s" % (section, suffix)

    lines = [
        "Inbox: ACTION_REQUIRED %s pending item(s) from %s."
        % (len(pending), section),
    ]
    for entry in pending[-limit:]:
        lines.append(
            "  - #%s %s %s: %s"
            % (
                entry["index"],
                entry["timestamp"],
                entry["digest"],
                summarize_body(entry["body"]),
            )
        )
    if len(pending) > limit:
        lines.append("  ... %s older pending item(s) omitted" % (len(pending) - limit))
    lines.append(
        "  reply: scripts/bridge-inbox.sh ack --self %s --status CLAIM --note '<what you will do>'"
        % info["self"]
    )
    return "\n".join(lines)


def ack(project, self_name, status_name, note="", peer_name=None, wait=10.0):
    status_name = status_name.upper()
    if status_name not in STATUSES:
        raise ValueError("--status must be one of: %s" % ", ".join(STATUSES))
    peer_name = peer_for(self_name, peer_name)
    paths = bc.collab_paths(project)
    run_id = "inbox-%s" % self_name
    if not bc.acquire_lock(paths["lock"], run_id, ttl=30, wait=wait):
        return "lockbusy", None
    try:
        entries = load_entries(project, self_name, peer_name)
        if not entries:
            return "empty", None
        latest = entries[-1]
        state = load_state(paths)
        record = {
            "self": self_name,
            "peer": peer_name,
            "section": "%s Outbox" % peer_name,
            "last_digest": latest["digest"],
            "last_index": latest["index"],
            "status": status_name,
            "note": note,
            "updated_at": bc.now_str(),
        }
        state[state_key(self_name, peer_name)] = record
        bc.atomic_write_json(paths["inbox_ack"], state)
        text = (
            "**%s** %s %s item #%s `%s`."
            % (self_name, status_name, record["section"], latest["index"], latest["digest"])
        )
        if note:
            text += "\n\nNote: %s" % note
        bc._append_under_header(paths["board"], "## Inbox Acks", text)
        prev = int((bc.read_json(paths["signal"], default={}) or {}).get("update_id", 0))
        bc.atomic_write_json(
            paths["signal"],
            {
                "update_id": prev + 1,
                "updated_at": bc.now_str(),
                "updated_by": self_name,
                "changed_section": "Inbox Acks",
                "summary": "%s %s %s item %s"
                % (self_name, status_name, record["section"], latest["digest"]),
            },
        )
        return "ok", record
    finally:
        bc.release_lock(paths["lock"], run_id)


def cmd_pending(args):
    project = bc.find_project_root(args.project)
    info = status(project, args.self_name, args.peer)
    print(format_pending(info, args.limit))
    return 1 if info["pending"] else 0


def cmd_ack(args):
    project = bc.find_project_root(args.project)
    result, record = ack(project, args.self_name, args.status, args.note, args.peer, args.wait)
    if result == "lockbusy":
        print("LOCKBUSY", file=sys.stderr)
        return 3
    if result == "empty":
        print("Inbox: EMPTY for %s." % inbox_section(args.self_name, args.peer))
        return 1
    print(
        "Inbox: %s %s item #%s %s"
        % (record["self"], record["status"], record["last_index"], record["last_digest"])
    )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect or acknowledge peer Outbox items.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pending = sub.add_parser("pending", help="Show unacknowledged items in my inbox.")
    pending.add_argument("--self", dest="self_name", required=True)
    pending.add_argument("--peer")
    pending.add_argument("--project", default=os.getcwd())
    pending.add_argument("--limit", type=int, default=5)
    pending.set_defaults(func=cmd_pending)

    ack_p = sub.add_parser("ack", help="Acknowledge all current inbox items through latest.")
    ack_p.add_argument("--self", dest="self_name", required=True)
    ack_p.add_argument("--peer")
    ack_p.add_argument("--project", default=os.getcwd())
    ack_p.add_argument("--status", choices=STATUSES, required=True)
    ack_p.add_argument("--note", default="")
    ack_p.add_argument("--wait", type=float, default=10.0)
    ack_p.set_defaults(func=cmd_ack)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
