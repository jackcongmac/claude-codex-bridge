"""_chat_server.py - per-project chat web server port and registry helpers."""
import hashlib
import json
import os
import socket

from bridge_common import collab_paths, find_project_root


def _start_path(root):
    return None if root is None else os.fspath(root)


def preferred_port(root):
    path = os.path.abspath(os.path.normpath(_start_path(root) or os.getcwd()))
    digest = hashlib.sha1(path.encode()).hexdigest()
    return 8765 + int(digest, 16) % 1000


def server_info_path(root):
    return os.path.join(
        collab_paths(find_project_root(_start_path(root)))["dir"],
        "chat_server.json")


def read_server_info(root):
    try:
        with open(server_info_path(root)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_server_info(root, port, pid, started_at):
    path = server_info_path(root)
    data = {
        "pid": int(pid),
        "port": int(port),
        "url": "http://127.0.0.1:%d" % int(port),
        "started_at": str(started_at),
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def clear_server_info(root, pid):
    try:
        info = read_server_info(root)
        if not info or int(info.get("pid")) != int(pid):
            return
        os.remove(server_info_path(root))
    except (OSError, TypeError, ValueError):
        pass


def is_running(info, *, alive, port_open):
    if not info:
        return False
    try:
        pid = int(info["pid"])
        port = int(info["port"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        return bool(alive(pid) and port_open(port))
    except Exception:
        return False


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ProcessLookupError):
        return False


def _port_open(port):
    try:
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=0.2)
    except (OSError, TypeError, ValueError):
        return False
    try:
        return True
    finally:
        sock.close()
