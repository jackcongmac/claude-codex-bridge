# DESIGN: project-rooted .collab/ layout + auto-location — v1

Status: **DRAFT v1 — for Codex review.** Fixes the gap that bit us live: the
collaboration files lived in one repo while the agents' windows were cwd'd in
another, so a fresh window couldn't find the board/protocol. Anyone downloading
the project would hit the same thing. The fix makes the coordination layer
**bind to the project root, auto-locate from any cwd depth, and live in one tidy
subdirectory** so users never guess a path.

## The problem (general, not specific to us)

- Coordination files (`collaboration.md`, `collaboration_signal.json`,
  `collaboration_state.json`, `collaboration_queue.json`,
  `collaboration_participants.json`, `collaboration_auto.log`,
  `collaboration_archive/`, `collaboration.lock`, `.watcher_*`, `.qagent_*`,
  `.heartbeat_*`) currently sit wherever `--project` points, default cwd.
- Scripts require an explicit `--project`; agents in a different cwd see nothing.
- The board location is a convention the user must remember → drift, "can't find
  the board", files scattered in the project root.

## The fix

### 1. One tidy subdirectory: `<project-root>/.collab/`
All coordination files move under `<root>/.collab/`. The project root stays clean
(no 8 loose files); one dotted dir clearly marks "this is the coordination layer."

```
<project-root>/
  AGENTS.md          # at ROOT (agents auto-read it) -> "join before working"
  CLAUDE.md          # at ROOT (Claude auto-reads)
  .collab/
    collaboration.md
    collaboration_signal.json
    collaboration_state.json
    collaboration_queue.json
    collaboration_participants.json
    collaboration_auto.log
    collaboration_archive/
    *.lock / .watcher_* / .qagent_* / .heartbeat_*   # runtime, gitignored
```

### 2. Auto-locate the project root from any cwd (boundary-safe)
`find_project_root(start)` walks UP from cwd, checking each ancestor IN ORDER, and
stops at the FIRST of these per directory:
- the dir contains **`.collab/`** → return it (an initialized project), OR
- the dir contains **`.git/`** (without `.collab/`) → return it as the
  (uninitialized) project root and **STOP — do not keep walking up to a parent
  `.collab/`**. This is the hard constraint Codex flagged: a nested git repo /
  submodule must never bind to a parent project's `.collab/`.
- else keep walking up; if none found, return cwd.
Explicit `--project` ALWAYS wins over auto-location.

### 3. `--project` becomes optional everywhere
Every script (`_auto_turn.py`, `_queue_turn.py`, `board-wait.sh`,
`join-collaboration.sh`, `_presence.py`, `_compact.py`, `compact-collaboration.sh`,
`watch-collaboration.sh`, `init-collaboration.sh`) defaults `--project` to
`find_project_root(cwd)` and reads/writes under `<root>/.collab/`. `--project`
still works to override.

### 4. `init` writes to the root it resolves
`init-collaboration.sh [dir]` resolves the root (given dir, else git-root, else
cwd), creates `<root>/.collab/` with the templates, and drops `AGENTS.md` +
`CLAUDE.md` at the root (so any agent opening the project auto-discovers the
protocol). It prints the resolved root so the user sees exactly where the layer
landed.

### 5. AGENTS.md / CLAUDE.md stay at the ROOT
They MUST be at the project root (that's what a fresh agent auto-reads). They
point at `.collab/` and tell the agent to run `join-collaboration.sh` (which
auto-locates). They are repo content (committed); the `.collab/` instances are
runtime (gitignored).

## Implementation notes

- Add `find_project_root()` + `collab_paths(root)` to `bridge_common.py` (single
  source of truth for where every coordination file lives). All Python scripts use
  it; the bash scripts get the root via a tiny `python3 -c` call or a shared
  `bridge-root.sh` helper.
- `.gitignore`: ignore `.collab/` entirely (it's all runtime), keep `AGENTS.md`,
  `CLAUDE.md`, and `templates/` tracked.
- Migration / back-compat: if a legacy flat `collaboration.md` exists in the root
  and no `.collab/`, scripts fall back to the flat root layout (so existing setups
  keep working); new `init` always creates `.collab/`. Optionally a one-liner
  `migrate` that moves flat files into `.collab/`.
- This is harness-lane (scripts/*, bridge_common, templates). Test suite must stay
  green; add tests for `find_project_root` (walk-up) and the `.collab/` paths.

## Why this fixes BOTH problems
- **General users:** download → `cd my-project` → `init-collaboration.sh` → a clean
  `.collab/` at the root + AGENTS.md; any agent in the project (any cwd depth)
  auto-finds it. No path guessing, no scattering, no "can't find the board."
- **Us, right now:** run `init` at the DWJX root → `.collab/` + AGENTS.md land
  there → the Codex window (cwd inside DWJX) auto-locates the board → we finally
  collaborate in the real working project instead of cd-ing into the bridge repo.

## Open questions for Codex
1. `.collab/` (dotted, hidden) vs `collab/` (visible) vs configurable — preference?
2. Root resolution order: `.collab/` ancestor first, then `.git/`, then cwd — is
   that the right precedence? Should an explicit `.collab-root` marker file win?
3. Back-compat: support legacy flat layout via fallback, or require a `migrate`?
   How long to keep the fallback?
4. Should `.collab/` be fully gitignored (runtime), or should a *template* board
   (empty) be committable so a repo can ship a pre-seeded protocol? (I lean:
   `.collab/` gitignored; ship templates/ + AGENTS.md only.)
5. Anything that breaks the 2-actor / queue / presence invariants when paths move
   under `.collab/` (lock path, signal path, high-water files must all relocate
   together and atomically — any split-brain risk?).
