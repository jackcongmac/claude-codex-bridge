"""Per-instance role config for chat-driven execution. INSTANCE DATA — lives in
.collab/roles.json (gitignored). No identity is hardcoded; defaults are neutral."""
import json
import os

from bridge_common import collab_paths, find_project_root

DEFAULTS = {"human": "Human", "lead": ""}
DEFAULT_AGENTS = ("Claude", "Codex")


def roles_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "roles.json")


def load_roles(project):
    out = dict(DEFAULTS)
    try:
        with open(roles_path(project)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for key in ("human", "lead"):
            if isinstance(data.get(key), str) and data[key]:
                out[key] = data[key]
        agents = data.get("agents")
        if isinstance(agents, list) and agents:
            out["agents"] = [a for a in agents if isinstance(a, str) and a]
    return out


def human_name(project):
    return load_roles(project)["human"]


def is_human(speaker, project):
    return bool(speaker) and speaker == human_name(project)


def lead_name(project):
    return load_roles(project)["lead"]


def _append_unique(out, seen, name, human):
    if not isinstance(name, str) or not name or name == human or name in seen:
        return
    seen.add(name)
    out.append(name)


def chat_peers(project):
    """Ordered, de-duplicated agent roster for the chat, NEVER empty."""
    roles = load_roles(project)
    human = roles.get("human")
    configured = roles.get("agents")
    # Registry auto-detect needs persistent agents distinguished from ephemeral workers.
    base = configured if isinstance(configured, list) and configured else list(DEFAULT_AGENTS)

    out = []
    seen = set()
    for name in base:
        _append_unique(out, seen, name, human)

    if not out:
        for name in DEFAULT_AGENTS:
            _append_unique(out, seen, name, human)
    if not out:
        return DEFAULT_AGENTS
    return tuple(out)
