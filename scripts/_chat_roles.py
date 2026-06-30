"""Per-instance role config for chat-driven execution. INSTANCE DATA — lives in
.collab/roles.json (gitignored). No identity is hardcoded; defaults are neutral."""
import json
import os

from bridge_common import collab_paths, find_project_root

DEFAULTS = {"human": "Human", "lead": ""}


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
    return out


def is_human(speaker, project):
    return bool(speaker) and speaker == load_roles(project)["human"]


def lead_name(project):
    return load_roles(project)["lead"]
