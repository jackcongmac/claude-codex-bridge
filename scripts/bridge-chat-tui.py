#!/usr/bin/env python3
"""bridge-chat-tui.py — terminal group chat for the shared ## Chat board.

TTY mode reads single keys: Enter sends, Esc exits. Non-TTY mode is line-oriented
for tests and scripting: each input line posts, /exit exits, and an ESC byte exits.
"""
import argparse
import importlib.util
import os
import select
import sys
import termios
import threading
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, read_json, read_section  # noqa: E402
from _post import post as _board_post  # noqa: E402


def _load_chatweb():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge-chat-web.py")
    spec = importlib.util.spec_from_file_location("chatweb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def post_chat_message(project, self_name, text):
    return _board_post(project, self_name, "**%s:** %s" % (self_name, text),
                       section="Chat", summary="chat: %s: %s" % (self_name, text))


def chat_update_id(project):
    p = collab_paths(find_project_root(project))
    sig = read_json(p["signal"], default={}) or {}
    return sig.get("update_id")


def render_chat(project, self_name, draft=""):
    chatweb = _load_chatweb()
    p = collab_paths(find_project_root(project))
    msgs = chatweb.parse_chat(read_section(p["board"], "Chat"))
    lines = ["群聊 · %s  (Enter 发送, Esc 退出)" % self_name, ""]
    typing = chatweb.chat_status(project).get("typing", [])
    if typing:
        lines.append("%s 正在思考..." % "、".join(typing))
        lines.append("")
    if not msgs:
        lines.append("(no messages yet)")
    else:
        for m in msgs[-30:]:
            lines.append("%s: %s" % (m["speaker"], m["text"]))
    lines.extend(["", "> %s" % draft])
    return "\n".join(lines)


def _start_responders(project, disabled):
    if disabled:
        return None, [], None, None
    chatweb = _load_chatweb()
    responders = chatweb.start_responders(project)
    stop_event = threading.Event()
    interval = float(os.environ.get("BRIDGE_CHAT_RESPONDER_SUPERVISE_INTERVAL", "5"))
    supervisor = None
    if responders:
        supervisor = threading.Thread(
            target=chatweb.supervise_responders,
            args=(responders, project, stop_event),
            kwargs={"interval": interval},
            daemon=True)
        supervisor.start()
    return chatweb, responders, stop_event, supervisor


def _stop_responders(chatweb, responders, stop_event, supervisor):
    if chatweb is not None:
        interval = float(os.environ.get("BRIDGE_CHAT_RESPONDER_SUPERVISE_INTERVAL", "5"))
        chatweb.shutdown_supervised_responders(
            responders, stop_event if responders else None, supervisor, join_timeout=interval + 2)


def run_line_mode(project, self_name):
    print(render_chat(project, self_name))
    for line in sys.stdin:
        if line.startswith("\x1b"):
            break
        text = line.rstrip("\n")
        if text.strip() in ("/exit", "/quit"):
            break
        if text.strip():
            st = post_chat_message(project, self_name, text.strip())
            if st != "ok":
                print("send failed: %s" % st, file=sys.stderr)
                return 1
    return 0


def consume_escape_sequence(stream, timeout=0.03, select_fn=select.select):
    """Return True if bytes after ESC look like a terminal escape sequence.

    Arrow/function keys start with ESC and then more bytes. A lone Esc has no immediate
    follow-up byte, so it remains the chat-exit key.
    """
    ready, _, _ = select_fn([stream], [], [], timeout)
    if not ready:
        return False
    while ready:
        ch = stream.read(1)
        if ch.isalpha() or ch == "~":
            break
        ready, _, _ = select_fn([stream], [], [], 0)
    return True


def run_tty(project, self_name, interval):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    draft = []
    last = None
    try:
        tty.setcbreak(fd)
        while True:
            uid = chat_update_id(project)
            if uid != last:
                print("\033[2J\033[H" + render_chat(project, self_name, "".join(draft)),
                      end="", flush=True)
                last = uid
            ready, _, _ = select.select([sys.stdin], [], [], interval)
            if not ready:
                continue
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                if consume_escape_sequence(sys.stdin):
                    continue
                break
            if ch in ("\x03", "\x04"):
                break
            if ch in ("\r", "\n"):
                text = "".join(draft).strip()
                draft = []
                if text:
                    st = post_chat_message(project, self_name, text)
                    if st != "ok":
                        print("\nsend failed: %s" % st, file=sys.stderr)
                        return 1
                    last = None
                continue
            if ch in ("\x7f", "\b"):
                if draft:
                    draft.pop()
            elif ch.isprintable():
                draft.append(ch)
            print("\033[2J\033[H" + render_chat(project, self_name, "".join(draft)),
                  end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self", dest="self_name", required=True)
    ap.add_argument("--project", default=None)
    ap.add_argument("--interval", type=float, default=float(os.environ.get("BRIDGE_CHAT_INTERVAL", "1")))
    ap.add_argument("--no-responders", action="store_true")
    a = ap.parse_args()

    project = find_project_root(a.project)
    chatweb, responders, stop_event, supervisor = _start_responders(project, a.no_responders)
    try:
        if sys.stdin.isatty():
            return run_tty(project, a.self_name, a.interval)
        return run_line_mode(project, a.self_name)
    finally:
        _stop_responders(chatweb, responders, stop_event, supervisor)


if __name__ == "__main__":
    sys.exit(main())
