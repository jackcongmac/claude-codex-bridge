#!/usr/bin/env python3
"""_chat_respond.py — the group-chat auto-responder (one pass).

An agent replies when the LATEST ## Chat message is from someone else AND is either
a HUMAN group message with no `@` (everyone replies) or @-mentions it (or @All). An
agent posting with no `@` compels no one. Never to itself. A consecutive-agent-turn
cap breaks ping-pong (a human message resets it). A spawned agent that replies "PASS"
stays silent. The reply is plain text posted back to ## Chat via the locked board write.

CLI: _chat_respond.py once --self <Agent> [--project DIR] [--max-turns N]
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root  # noqa: E402
from _post import post as _board_post  # noqa: E402

# reuse parse_chat + mentions from the (hyphen-named) web-chat module
_spec = importlib.util.spec_from_file_location(
    "chatweb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge-chat-web.py"))
_cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cw)
parse_chat, mentions = _cw.parse_chat, _cw.mentions

AGENTS = {"Claude", "Codex"}


def _chat_section_text(text):
    m = re.search(r'(?m)^## Chat[ \t]*$', text)
    if not m:
        return ""
    i = m.start()
    j = text.find("\n## ", i + len("## Chat"))
    return text[i:] if j == -1 else text[i:j]


def _chat_section(board_path):
    try:
        with open(board_path) as f:
            return _chat_section_text(f.read())
    except OSError:
        return ""


_KEY = ("ts", "speaker", "text")


def _same_msg(a, b):
    return a is not None and b is not None and tuple(a.get(k) for k in _KEY) == tuple(b.get(k) for k in _KEY)


def _targets(msg):
    """Who MUST reply to this message:
    - a HUMAN message with NO @ is a group message → everyone (both agents) replies;
    - @X → X (the other agent may simply stay quiet);
    - an AGENT message compels a reply ONLY via an explicit @, so two agents don't
      ping-pong forever on plain chatter (you watch them; they @ you when they want you).
    """
    who = mentions(msg.get("text", ""))
    if not who and msg.get("speaker") not in AGENTS:
        return set(AGENTS)
    return who


def _select_prompt(msgs, self_name):
    """Return (prompt_msg, status) for the current pass.

    Normally the latest message is the prompt. The exception is trailing agent chatter
    with no @: that can be the first responder's answer to a human group message. A
    delayed second responder must still answer the original prompt unless it already
    spoke after that prompt.
    """
    latest = msgs[-1]
    if latest["speaker"] == self_name:
        return None, "self"
    if self_name in _targets(latest):
        return latest, None
    if latest["speaker"] not in AGENTS or _targets(latest):
        return None, "not-addressed"

    spoke_after = False
    for msg in reversed(msgs[:-1]):
        if msg["speaker"] == self_name:
            spoke_after = True
        if msg["speaker"] in AGENTS and not _targets(msg):
            continue
        if self_name in _targets(msg) and not spoke_after:
            return msg, None
        return None, "not-addressed"
    return None, "not-addressed"


def _prompt(self_name, msgs, prompt_msg):
    other = "Codex" if self_name == "Claude" else "Claude"
    convo = "\n".join("%s: %s" % (m["speaker"], m["text"]) for m in msgs)
    return (
        "You are %s, one of two AI agents (Claude, Codex) in a group chat with the "
        "human. Reply BRIEFLY (1-3 sentences) to this message, which is addressed "
        "to you. To pass the turn, @-mention someone: @%s (the other agent), @<human>, "
        "or @All. If you have nothing useful to add, reply with exactly: PASS\n\n"
        "Message to answer:\n%s: %s\n\nConversation so far:\n%s\n\nYour reply as %s:"
        % (self_name, other, prompt_msg["speaker"], prompt_msg["text"], convo, self_name))


def _spawn_claude(prompt, project):
    claude = os.environ.get("CLAUDE_BIN") or "claude"
    cmd = [claude, "-p", prompt, "--output-format", "json", "--strict-mcp-config",
           "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "default",
           "--allowedTools", "Read", "Grep", "Glob"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=project,
                           timeout=int(os.environ.get("BRIDGE_CHAT_TURN_TIMEOUT", "180")))
        return (json.loads(r.stdout).get("result") or "").strip()
    except Exception:
        return ""


def _spawn_codex(prompt, project):
    codex = os.environ.get("CODEX_BIN") or "codex"
    last = os.path.join(tempfile.mkdtemp(), "last.txt")
    cmd = [codex, "exec", prompt, "--output-last-message", last, "-C", project,
           "--skip-git-repo-check", "--ignore-user-config", "-s", "read-only"]
    try:
        subprocess.run(cmd, capture_output=True, text=True,
                       timeout=int(os.environ.get("BRIDGE_CHAT_TURN_TIMEOUT", "180")))
        with open(last) as f:
            return f.read().strip()
    except Exception:
        return ""


def _default_runner(self_name):
    return _spawn_claude if self_name == "Claude" else _spawn_codex


def respond_once(project, self_name, max_turns=6, runner=None):
    """One pass: reply iff the latest message is someone else's and targets me (a human
    group message with no @, or an explicit @me / @All) and the agent-turn cap isn't hit.
    Returns a status string (empty/self/not-addressed/capped/passed/responded)."""
    project = find_project_root(project)
    p = collab_paths(project)
    msgs = parse_chat(_chat_section(p["board"]))
    if not msgs:
        return "empty"
    latest = msgs[-1]
    prompt_msg, status = _select_prompt(msgs, self_name)
    if status:
        return status
    streak = 0                                  # consecutive trailing agent messages
    for m in reversed(msgs):
        if m["speaker"] in AGENTS:
            streak += 1
        else:
            break
    if streak >= max_turns:
        return "capped"
    reply = ((runner or _default_runner(self_name))(_prompt(self_name, msgs, prompt_msg), project) or "").strip()
    if not reply or reply.upper() == "PASS":
        return "passed"
    # Spawning the agent took seconds. If a newer message landed meanwhile, this reply
    # is stale — drop it rather than bury the new message under our answer. The guard
    # runs UNDER the post lock, so the tail-check and the append are atomic: no message
    # can race in between (every writer holds the same lock). A dropped reply is fine —
    # the newer message bumped the signal, so the loop re-fires and answers it fresh.
    def _guard(board_text):
        tail = parse_chat(_chat_section_text(board_text))
        if not tail:
            return False
        newest = tail[-1]
        if _same_msg(newest, latest):
            return True                    # tail unchanged → post
        # A newer message arrived during the (slow) spawn. DROP this reply only if that
        # newer message is a fresh prompt addressed to ME — the loop will answer it
        # instead. If it's the other agent's parallel reply or chatter not for me, my
        # answer to the original prompt is still valid, so post it (replies may interleave).
        return not (newest.get("speaker") != self_name and self_name in _targets(newest))

    st = _board_post(project, self_name, "**%s:** %s" % (self_name, reply),
                     section="Chat", guard=_guard)
    if st == "superseded":
        return "superseded"
    # "lockbusy" means NOTHING was written — surface it so the loop retries this
    # message instead of advancing past it. "ok"/"signal_failed" both leave the reply
    # on the board (signal_failed just missed the wake, which the next bump fixes).
    return "lockbusy" if st == "lockbusy" else "responded"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once")
    o.add_argument("--self", dest="self_name", required=True)
    o.add_argument("--project", default=None)
    o.add_argument("--max-turns", type=int,
                   default=int(os.environ.get("BRIDGE_CHAT_MAX_TURNS", "6")))
    a = ap.parse_args()
    st = respond_once(a.project, a.self_name, a.max_turns)
    print(st)
    # Exit non-zero ONLY for a transient failure (lock busy) so the loop retries
    # this message; every other status means the message is handled or moot.
    return 3 if st == "lockbusy" else 0


if __name__ == "__main__":
    sys.exit(main())
