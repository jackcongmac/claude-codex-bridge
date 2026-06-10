#!/usr/bin/env python3
"""Apply a claude-codex-bridge role preset to collaboration_state.json."""

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = ROOT / "presets"
KNOWN_ROLES = {"peer", "planner", "executor", "reviewer"}
KNOWN_ACTORS = {"Claude", "Codex"}
ALLOWED_TOP_LEVEL = {
    "name",
    "description",
    "roles",
    "resource_profiles",
    "routing_rules",
}


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def preset_path(name):
    candidate = PRESET_DIR / f"{name}.json"
    if not candidate.is_file():
        raise SystemExit(f"unknown preset: {name}")
    return candidate


def validate_resource_profiles(profiles):
    if set(profiles) != KNOWN_ACTORS:
        raise SystemExit("resource_profiles must define exactly Claude and Codex")
    for actor, profile in profiles.items():
        if not isinstance(profile, dict):
            raise SystemExit(f"resource profile for {actor} must be an object")
        for key in ("tier", "best_for", "avoid"):
            if key not in profile:
                raise SystemExit(f"resource profile for {actor} missing {key}")
        if not isinstance(profile["best_for"], list) or not isinstance(profile["avoid"], list):
            raise SystemExit(f"resource profile for {actor} best_for/avoid must be lists")


def validate_preset(preset):
    unknown = set(preset) - ALLOWED_TOP_LEVEL
    if unknown:
        raise SystemExit(f"unknown preset fields: {', '.join(sorted(unknown))}")
    if not preset.get("name"):
        raise SystemExit("preset missing name")
    roles = preset.get("roles")
    if not isinstance(roles, dict) or set(roles) != KNOWN_ACTORS:
        raise SystemExit("roles must define exactly Claude and Codex")
    for actor, role in roles.items():
        if actor not in KNOWN_ACTORS or role not in KNOWN_ROLES:
            raise SystemExit(f"invalid role mapping: {actor}={role}")
    if "resource_profiles" in preset:
        validate_resource_profiles(preset["resource_profiles"])


def apply_preset(project, preset_name):
    state_path = project / "collaboration_state.json"
    if not state_path.is_file():
        raise SystemExit(f"missing collaboration_state.json in {project}")

    preset = load_json(preset_path(preset_name))
    validate_preset(preset)
    state = load_json(state_path)

    state["roles"] = preset["roles"]
    if "resource_profiles" in preset:
        state["resource_profiles"] = preset["resource_profiles"]

    write_json(state_path, state)
    return preset["name"], state_path


def main():
    parser = argparse.ArgumentParser(
        description="Apply a role preset to a project's collaboration_state.json."
    )
    parser.add_argument("--project", default=".", help="Project directory containing collaboration_state.json.")
    parser.add_argument("--preset", required=True, help="Preset name from presets/*.json.")
    args = parser.parse_args()

    name, state_path = apply_preset(Path(args.project).resolve(), args.preset)
    print(f"[ok] applied role preset {name} to {state_path}")


if __name__ == "__main__":
    main()
