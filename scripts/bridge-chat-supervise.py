#!/usr/bin/env python3
"""bridge-chat-supervise.py — keep group-chat responders alive without opening a UI.

This is the always-on companion to the web/TUI chat launchers. It starts one Claude
responder and one Codex responder for the project, restarts either one if it exits,
and stops both process groups on Ctrl-C/SIGTERM.
"""
import argparse
import importlib.util
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_common as bc  # noqa: E402


AGENTS = ("Claude", "Codex")


def _load_chatweb():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "chatweb", os.path.join(here, "bridge-chat-web.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_stop_handler(stop_event):
    def _handler(signum, frame):
        stop_event.set()
    return _handler


def _install_signal_handlers(stop_event):
    previous = {}
    handler = _make_stop_handler(stop_event)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (OSError, ValueError):
            pass
    return previous


def _restore_signal_handlers(previous):
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            pass


def _wait_until_stopped(stop_event):
    while not stop_event.wait(1):
        pass


def _stamp(epoch=None):
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(epoch or time.time()))


def _state_path(project):
    return os.path.join(bc.collab_paths(bc.find_project_root(project))["dir"],
                        "chat_supervisor.json")


def new_tracker():
    return {
        "agents": {
            name: {
                "alive": False,
                "pid": None,
                "last_start_at": None,
                "last_exit_code": None,
                "restart_count": 0,
                "failure_count": 0,
                "alive_since": None,
                "backoff_until": None,
                "first_down_at": None,
            }
            for name in AGENTS
        },
        "alerts": [],
    }


def write_supervisor_state(project, tracker, handles, now=None):
    now = time.time() if now is None else now
    agents = {}
    for idx, name in enumerate(AGENTS):
        rec = dict(tracker["agents"].get(name, {}))
        handle = handles[idx] if idx < len(handles) else None
        rec["pid"] = getattr(handle, "pid", rec.get("pid", None))
        agents[name] = rec
    state = {
        "updated_at": _stamp(now),
        "supervisor": {
            "pid": os.getpid(),
            "status": "running",
            "heartbeat_at": _stamp(now),
            "self_recovery": "not automatic; stale heartbeat requires human restart",
        },
        "agents": agents,
        "alerts": list(tracker.get("alerts", [])),
    }
    bc.atomic_write_json(_state_path(project), state)
    return state


def emit_alert(project, tracker, message, notify=None):
    if message in tracker.setdefault("alerts", []):
        return False
    P = bc.collab_paths(bc.find_project_root(project))
    if not bc.acquire_lock(P["lock"], "chat-supervisor-alert-%d" % os.getpid(), ttl=30, wait=10):
        return False
    try:
        bc._append_under_header(P["board"], "## Liveness", "**chat supervisor:** %s" % message)
        sig = bc.read_json(P["signal"], {}) or {}
        bc.atomic_write_json(P["signal"], {
            "update_id": int(sig.get("update_id", 0)) + 1,
            "updated_at": bc.now_str(),
            "updated_by": "chat-supervisor",
            "changed_section": "Liveness",
            "summary": "chat supervisor: %s" % message,
        })
        tracker["alerts"].append(message)
    finally:
        bc.release_lock(P["lock"], "chat-supervisor-alert-%d" % os.getpid())
    try:
        (notify or bc.notify)("chat supervisor: %s" % message)
    except Exception:
        pass
    return True


def _responder_names_from_cmds(cmds):
    names = []
    for cmd in cmds:
        try:
            names.append(cmd[cmd.index("--self") + 1])
        except (ValueError, IndexError):
            names.append("")
    return names


def supervisor_cycle(handles, tracker, project, scripts_dir=None, chatweb=None, now=None,
                     spawn=None, max_restarts=5, backoff_base=1.0, backoff_max=60.0,
                     stale_after=300.0, healthy_after=None, notify=None):
    chatweb = chatweb or _load_chatweb()
    scripts_dir = scripts_dir or os.path.dirname(os.path.abspath(__file__))
    now = time.time() if now is None else now
    spawn = spawn or (lambda cmd: chatweb.subprocess.Popen(
        cmd, stdout=chatweb.subprocess.DEVNULL, stderr=chatweb.subprocess.DEVNULL,
        start_new_session=True))
    healthy_after = float(healthy_after if healthy_after is not None else backoff_max)
    cmds = chatweb.responder_cmds(scripts_dir, project)
    names = _responder_names_from_cmds(cmds)
    for idx, cmd in enumerate(cmds):
        name = names[idx]
        if not name:
            continue
        while idx >= len(handles):
            handles.append(None)
        rec = tracker["agents"].setdefault(name, {})
        handle = handles[idx]
        alive = chatweb._responder_handle_alive(handle, project, name)
        rec["pid"] = getattr(handle, "pid", None)
        if alive:
            rec["alive_since"] = rec.get("alive_since") or now
            rec["alive"] = True
            if now - rec["alive_since"] >= healthy_after:
                rec["failure_count"] = 0
                rec["first_down_at"] = None
                rec["backoff_until"] = None
            rec["last_exit_code"] = None
            continue

        rec["alive"] = False
        rec["alive_since"] = None
        rec["last_exit_code"] = getattr(handle, "poll", lambda: None)()
        rec["first_down_at"] = rec.get("first_down_at") or now
        if rec.get("backoff_until") and now < rec["backoff_until"]:
            if now - rec["first_down_at"] >= stale_after:
                emit_alert(project, tracker, "%s responder stale during backoff" % name, notify=notify)
            continue
        if rec.get("failure_count", 0) >= max_restarts:
            emit_alert(project, tracker, "%s responder restart limit reached" % name, notify=notify)
            continue

        new_handle = spawn(cmd)
        handles[idx] = new_handle
        rec["alive"] = True
        rec["pid"] = getattr(new_handle, "pid", None)
        rec["alive_since"] = now
        rec["last_start_at"] = _stamp(now)
        rec["restart_count"] = int(rec.get("restart_count", 0)) + 1
        rec["failure_count"] = int(rec.get("failure_count", 0)) + 1
        delay = min(float(backoff_base) * (2 ** (rec["failure_count"] - 1)), float(backoff_max))
        rec["backoff_until"] = now + delay
    write_supervisor_state(project, tracker, handles, now=now)
    return tracker


def supervision_loop(handles, tracker, project, stop_event, interval=None, heartbeat_interval=None,
                     scripts_dir=None, chatweb=None, max_restarts=5, backoff_base=1.0,
                     backoff_max=60.0, stale_after=300.0, healthy_after=None):
    interval = float(interval if interval is not None else 5)
    heartbeat_interval = float(heartbeat_interval if heartbeat_interval is not None else interval)
    next_heartbeat = 0.0
    while not stop_event.is_set():
        now = time.time()
        supervisor_cycle(
            handles, tracker, project, scripts_dir=scripts_dir, chatweb=chatweb, now=now,
            max_restarts=max_restarts, backoff_base=backoff_base,
            backoff_max=backoff_max, stale_after=stale_after,
            healthy_after=healthy_after)
        if now >= next_heartbeat:
            write_supervisor_state(project, tracker, handles, now=now)
            next_heartbeat = now + heartbeat_interval
        stop_event.wait(interval)


def run_supervisor(project, scripts_dir=None, interval=None, heartbeat_interval=None,
                   stale_after=None, max_restarts=None, backoff_base=None,
                   backoff_max=None, healthy_after=None, chatweb=None, wait=None):
    chatweb = chatweb or _load_chatweb()
    project = chatweb.find_project_root(project)
    interval = float(interval if interval is not None
                     else os.environ.get("BRIDGE_CHAT_RESPONDER_SUPERVISE_INTERVAL", "5"))
    heartbeat_interval = float(heartbeat_interval if heartbeat_interval is not None
                               else os.environ.get("BRIDGE_CHAT_SUPERVISOR_HEARTBEAT", "30"))
    stale_after = float(stale_after if stale_after is not None
                        else os.environ.get("BRIDGE_CHAT_SUPERVISOR_STALE", "300"))
    max_restarts = int(max_restarts if max_restarts is not None
                       else os.environ.get("BRIDGE_CHAT_SUPERVISOR_MAX_RESTARTS", "5"))
    backoff_base = float(backoff_base if backoff_base is not None
                         else os.environ.get("BRIDGE_CHAT_SUPERVISOR_BACKOFF_BASE", "1"))
    backoff_max = float(backoff_max if backoff_max is not None
                        else os.environ.get("BRIDGE_CHAT_SUPERVISOR_BACKOFF_MAX", "60"))
    healthy_after = float(healthy_after if healthy_after is not None
                          else os.environ.get("BRIDGE_CHAT_SUPERVISOR_HEALTHY_AFTER", str(backoff_max)))
    stop_event = threading.Event()
    previous_handlers = _install_signal_handlers(stop_event)
    responders = chatweb.start_responders(project, scripts_dir=scripts_dir)
    tracker = new_tracker()
    supervisor = threading.Thread(
        target=supervision_loop,
        args=(responders, tracker, project, stop_event),
        kwargs={
            "interval": interval,
            "heartbeat_interval": heartbeat_interval,
            "scripts_dir": scripts_dir,
            "chatweb": chatweb,
            "max_restarts": max_restarts,
            "backoff_base": backoff_base,
            "backoff_max": backoff_max,
            "stale_after": stale_after,
            "healthy_after": healthy_after,
        },
        daemon=True)
    supervisor.start()
    try:
        (wait or _wait_until_stopped)(stop_event)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        chatweb.shutdown_supervised_responders(
            responders, stop_event, supervisor, join_timeout=interval + 2)
        _restore_signal_handlers(previous_handlers)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--interval", type=float, default=None,
                    help="responder supervision interval in seconds")
    ap.add_argument("--heartbeat-interval", type=float, default=None,
                    help="chat_supervisor.json heartbeat interval in seconds")
    ap.add_argument("--stale-after", type=float, default=None,
                    help="seconds down/backing off before a Liveness alert")
    ap.add_argument("--max-restarts", type=int, default=None,
                    help="per-agent restart failures before alerting instead of hot-looping")
    ap.add_argument("--backoff-base", type=float, default=None,
                    help="base seconds for exponential restart backoff")
    ap.add_argument("--backoff-max", type=float, default=None,
                    help="maximum seconds for restart backoff")
    ap.add_argument("--healthy-after", type=float, default=None,
                    help="seconds continuously alive before restart failures reset")
    ap.add_argument("--scripts-dir", default=None,
                    help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    project = a.project or os.getcwd()
    print("[*] supervising group-chat responders for %s" % project)
    print("    Ctrl-C or SIGTERM stops Claude/Codex responders cleanly.")
    print("    State: <project>/.collab/chat_supervisor.json")
    return run_supervisor(
        project,
        scripts_dir=a.scripts_dir,
        interval=a.interval,
        heartbeat_interval=a.heartbeat_interval,
        stale_after=a.stale_after,
        max_restarts=a.max_restarts,
        backoff_base=a.backoff_base,
        backoff_max=a.backoff_max,
        healthy_after=a.healthy_after)


if __name__ == "__main__":
    sys.exit(main())
