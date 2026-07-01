#!/usr/bin/env python3
"""_chat_respond.py — the group-chat auto-responder (one pass).

An agent replies when ## Chat contains an unhandled message for it: a HUMAN group
message with no `@` (everyone replies) or a message that @-mentions it (or @All).
On restart it scans older missed prompts before giving up, and records handled
message ids in .collab/chat_delivery.json so offline @ messages don't sink forever
with best-effort dedupe. It records .collab/chat_typing.json while a model is
thinking so the web room can show progress. An agent posting with no `@` compels
no one. Never to itself. A consecutive-agent-turn cap breaks ping-pong (a human
message resets it). A spawned agent that replies "PASS" stays silent. The reply
is plain text posted back to ## Chat via the locked board write.

CLI: _chat_respond.py once --self <Agent> [--project DIR] [--max-turns N]
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, read_json, atomic_write_json, now_str,
    acquire_lock, release_lock,
)
from _post import post as _board_post  # noqa: E402
import _agent_cli  # noqa: E402
import _chat_roles  # noqa: E402

# reuse parse_chat + mentions from the (hyphen-named) web-chat module
_spec = importlib.util.spec_from_file_location(
    "chatweb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge-chat-web.py"))
_cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cw)
parse_chat, mentions = _cw.parse_chat, _cw.mentions
format_chat_message = _cw.format_chat_message

DEFAULT_AGENTS = _chat_roles.DEFAULT_AGENTS


def _speaker_base(msg):
    if not isinstance(msg, dict):
        return ""
    if msg.get("speaker_base"):
        return msg.get("speaker_base")
    parsed = _cw.parse_worker_speaker(msg.get("speaker", ""))
    return parsed[0] if parsed else msg.get("speaker", "")


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


def _message_id(msg):
    if msg.get("_id"):
        raw = json.dumps(["id", msg.get("_id")], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    parts = [msg.get(k, "") for k in _KEY]
    if "_dup" in msg:
        parts.append(msg.get("_dup"))
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _delivery_path(project):
    return collab_paths(project)["chat_delivery"]


def _typing_path(project):
    return collab_paths(project)["chat_typing"]


def _image_path_for(project, msg):
    """Return the absolute path to a chat message's image, or None."""
    ref = (msg or {}).get("img")
    if not ref or not re.match(r"^[0-9a-f]{8,}\.(png|jpg|gif|webp)$", ref):
        return None
    p = os.path.join(collab_paths(find_project_root(project))["dir"], "chat_uploads", ref)
    return p if os.path.exists(p) else None


def _normalize_delivery(delivery):
    if not isinstance(delivery, dict):
        delivery = {}

    messages = delivery.get("messages", {})
    delivery["messages"] = messages if isinstance(messages, dict) else {}

    agents = delivery.get("agents", {})
    normalized_agents = {}
    if isinstance(agents, dict):
        for name, agent in agents.items():
            if not isinstance(agent, dict):
                continue
            handled = agent.get("handled", {})
            clean_agent = dict(agent)
            clean_agent["handled"] = handled if isinstance(handled, dict) else {}
            normalized_agents[name] = clean_agent
    delivery["agents"] = normalized_agents
    return delivery


def _load_delivery(project):
    try:
        delivery = read_json(_delivery_path(project), default={"agents": {}, "messages": {}}) or {}
    except RuntimeError:
        delivery = {}
    return _normalize_delivery(delivery)


def _handled_ids(delivery, self_name):
    agent = (delivery.get("agents", {}) or {}).get(self_name, {})
    if not isinstance(agent, dict):
        return set()
    handled = agent.get("handled", {}) or {}
    return set(handled.keys()) if isinstance(handled, dict) else set()


def _mark_delivery(project, self_name, msg, status, agents=None):
    p = collab_paths(project)
    if not acquire_lock(p["lock"], "chat-delivery-%d" % os.getpid(), ttl=30, wait=10):
        return
    try:
        delivery = _load_delivery(project)
        mid = _message_id(msg)
        delivery.setdefault("messages", {})[mid] = {
            "ts": msg.get("ts", ""),
            "speaker": msg.get("speaker", ""),
            "text": msg.get("text", ""),
            "targets": sorted(_targets(msg, agents)),
        }
        if msg.get("_id"):
            delivery["messages"][mid]["id"] = msg.get("_id")
        if "_dup" in msg:
            delivery["messages"][mid]["duplicate"] = msg.get("_dup")
        agent = delivery.setdefault("agents", {}).setdefault(self_name, {"handled": {}})
        agent.setdefault("handled", {})[mid] = {"status": status, "at": now_str()}
        atomic_write_json(_delivery_path(project), delivery)
    finally:
        release_lock(p["lock"], "chat-delivery-%d" % os.getpid())


def _load_typing(project):
    try:
        state = read_json(_typing_path(project), default={"agents": {}}) or {"agents": {}}
    except RuntimeError:
        state = {"agents": {}}
    if not isinstance(state, dict):
        state = {"agents": {}}
    if not isinstance(state.get("agents", {}), dict):
        state["agents"] = {}
    return state


def _set_typing(project, self_name, msg):
    p = collab_paths(project)
    if not acquire_lock(p["lock"], "chat-typing-%d" % os.getpid(), ttl=30, wait=10):
        return
    try:
        state = _load_typing(project)
        state.setdefault("agents", {})[self_name] = {
            "status": "thinking",
            "message_id": _message_id(msg),
            "since": now_str(),
        }
        atomic_write_json(_typing_path(project), state)
    finally:
        release_lock(p["lock"], "chat-typing-%d" % os.getpid())


def _clear_typing(project, self_name):
    p = collab_paths(project)
    if not acquire_lock(p["lock"], "chat-typing-%d" % os.getpid(), ttl=30, wait=10):
        return
    try:
        state = _load_typing(project)
        state.setdefault("agents", {}).pop(self_name, None)
        atomic_write_json(_typing_path(project), state)
    finally:
        release_lock(p["lock"], "chat-typing-%d" % os.getpid())


def _targets(msg, agents=None):
    """Who MUST reply to this message:
    - a HUMAN message with NO @ is a group message → everyone (both agents) replies;
    - @X → X (the other agent may simply stay quiet);
    - an AGENT message compels a reply ONLY via an explicit @, so two agents don't
      ping-pong forever on plain chatter (you watch them; they @ you when they want you).
    """
    agents = tuple(agents or DEFAULT_AGENTS)
    who = mentions(msg.get("text", ""), peers=agents)
    if not who and _speaker_base(msg) not in agents:
        return set(agents)
    return who


def _self_spoke_after(msgs, idx, self_name):
    return any(_speaker_base(m) == self_name for m in msgs[idx + 1:])


def _select_pending_prompt(msgs, self_name, handled, agents):
    infer_handled_from_board = not handled
    for idx, msg in enumerate(msgs):
        if _speaker_base(msg) == self_name:
            continue
        if _message_id(msg) in handled:
            continue
        if (self_name in _targets(msg, agents)
                and not (infer_handled_from_board and _self_spoke_after(msgs, idx, self_name))):
            return msg
    return None


def _select_prompt(msgs, self_name, handled=None, agents=None):
    """Return (prompt_msg, status) for the current pass.

    Normally the latest message is the prompt. The exception is trailing agent chatter
    with no @: that can be the first responder's answer to a human group message. A
    delayed second responder must still answer the original prompt unless it already
    spoke after that prompt. A durable handled set lets a restarted responder scan
    older missed prompts so an offline @ message does not sink behind later chat.
    """
    handled = handled or set()
    agents = tuple(agents or DEFAULT_AGENTS)
    pending = _select_pending_prompt(msgs, self_name, handled, agents)
    if pending is not None:
        return pending, None
    latest = msgs[-1]
    if _speaker_base(latest) == self_name:
        return None, "self"
    if self_name in _targets(latest, agents) and _message_id(latest) not in handled:
        return latest, None
    if _speaker_base(latest) not in agents or _targets(latest, agents):
        return None, "not-addressed"

    spoke_after = False
    for msg in reversed(msgs[:-1]):
        if _speaker_base(msg) == self_name:
            spoke_after = True
        if _speaker_base(msg) in agents and not _targets(msg, agents):
            continue
        if self_name in _targets(msg, agents) and not spoke_after and _message_id(msg) not in handled:
            return msg, None
        return None, "not-addressed"
    return None, "not-addressed"


def _prompt(self_name, msgs, prompt_msg, agents=None):
    agents = tuple(agents or DEFAULT_AGENTS)
    convo = "\n".join("%s: %s" % (m["speaker"], m["text"]) for m in msgs)
    if agents == DEFAULT_AGENTS:
        identity = "one of two AI agents (Claude, Codex)"
        other = "Codex" if self_name == "Claude" else "Claude"
    else:
        identity = "one of the AI agents (%s)" % ", ".join(agents)
        other_agents = [name for name in agents if name != self_name]
        other = other_agents[0] if other_agents else "another agent"
    return (
        "You are %s, %s in a group chat with the "
        "human. Reply BRIEFLY (1-3 sentences) to this message, which is addressed "
        "to you. If you have nothing useful to add, reply with exactly: PASS\n\n"
        "DO NOT @-mention the other agent (@%s) unless you genuinely need them to act "
        "on something specific — an @ forces them to reply and creates agent-to-agent "
        "ping-pong. To stay silent, reply exactly PASS; never @ someone just to pass "
        "the turn. Only @ the human if you are directly asking them something.\n\n"
        "IDENTITY: You are the disposable READ-ONLY group-chat RESPONDER instance for "
        "%s, spawned only to answer chat. You can read the repo but CANNOT write files, "
        "run write/commit commands, or implement code. You are NOT the human's "
        "interactive coding pane. If asked about write access, sandbox, or implementing "
        "code, do NOT tell the human to reopen/relaunch you with workspace-write — that "
        "does not change this responder; real code changes happen in the human's "
        "interactive pane, not here. State this plainly instead of claiming you will "
        "write code.\n\n"
        "Message to answer:\n%s: %s\n\nConversation so far:\n%s\n\nYour reply as %s:"
        % (self_name, identity, other, self_name,
           prompt_msg["speaker"], prompt_msg["text"], convo, self_name))


def _spawn_claude(prompt, project, image_path=None):
    if image_path:
        prompt = prompt + (
            "\n\n[The user attached an image at: %s — use your Read tool to view it before replying.]"
            % image_path)
    return _agent_cli.run_claude_chat(prompt, project, image_path=image_path)


def _spawn_codex(prompt, project, image_path=None):
    return _agent_cli.run_codex_chat(prompt, project, image_path=image_path)


def _default_runner(self_name):
    return _spawn_claude if self_name == "Claude" else _spawn_codex


def respond_once(project, self_name, max_turns=6, runner=None):
    """One pass: reply iff the latest message is someone else's and targets me (a human
    group message with no @, or an explicit @me / @All) and the agent-turn cap isn't hit.
    Returns a status string (disabled/empty/self/not-addressed/capped/passed/responded)."""
    # The disposable auto-responder is OFF by default. A read-only responder posting
    # under the agent's own name confuses humans and the peer about who actually holds
    # a task (platform diagnosis 2026-06-17, first cut). Real interactive panes post via
    # bridge-chat/bridge-post and are unaffected. Opt in with BRIDGE_CHAT_AUTORESPOND=1.
    if os.environ.get("BRIDGE_CHAT_AUTORESPOND") != "1":
        return "disabled"
    project = find_project_root(project)
    agents = _chat_roles.chat_peers(project)
    p = collab_paths(project)
    msgs = parse_chat(_chat_section(p["board"]))
    if not msgs:
        return "empty"
    latest = msgs[-1]
    delivery = _load_delivery(project)
    prompt_msg, status = _select_prompt(
        msgs, self_name, _handled_ids(delivery, self_name), agents=agents)
    if status:
        return status
    streak = 0                                  # consecutive trailing agent messages
    for m in reversed(msgs):
        if _speaker_base(m) in agents:
            streak += 1
        else:
            break
    if streak >= max_turns:
        return "capped"
    _set_typing(project, self_name, prompt_msg)
    try:
        image_path = _image_path_for(project, prompt_msg)
        reply = ((runner or _default_runner(self_name))(
            _prompt(self_name, msgs, prompt_msg, agents=agents), project, image_path) or "").strip()
    finally:
        _clear_typing(project, self_name)
    if not reply or reply.upper() == "PASS":
        _mark_delivery(project, self_name, prompt_msg, "passed", agents=agents)
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
        return not (_speaker_base(newest) != self_name and self_name in _targets(newest, agents))

    st = _board_post(project, self_name, format_chat_message(self_name, reply),
                     section="Chat", guard=_guard)
    if st == "superseded":
        return "superseded"
    # "lockbusy" means NOTHING was written — surface it so the loop retries this
    # message instead of advancing past it. "ok"/"signal_failed" both leave the reply
    # on the board (signal_failed just missed the wake, which the next bump fixes).
    if st == "lockbusy":
        return "lockbusy"
    _mark_delivery(project, self_name, prompt_msg, "responded", agents=agents)
    return "responded"


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
