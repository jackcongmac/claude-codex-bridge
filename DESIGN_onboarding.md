# DESIGN: one-command install + one-phrase project setup — v1

Status: **DRAFT v1 — for Codex review.** Fixes the UX gap a real user hit: after
downloading, the transport installs but the skill doesn't, nothing makes a fresh
project "just work," and the user is left assembling pieces by hand. The mechanics
must not be dumped on the user.

## The two problems

1. **Download → install must set up EVERYTHING, automatically.** Today `install.sh`
   installs only the transport (the two MCP servers). The skill (`skill/SKILL.md`)
   is never installed into `~/.claude/skills/`, so it isn't discoverable, and the
   user doesn't even know the bridge is "a thing you invoke." One command should
   leave a working, discoverable install.
2. **A fresh project in a new folder needs a one-phrase trigger.** When a user opens
   a brand-new folder and wants the two agents to collaborate, they should say ONE
   simple thing and the collaboration layer (`.collab/` + `AGENTS.md`/`CLAUDE.md`)
   gets created — without the user running scripts or knowing the file names.

## Fix 1 — `install.sh` installs everything + tells you how to use it

- Keep: transport (wrapper, `~/.codex/config.toml` block, `claude mcp add codex`).
- ADD: install the skill so it's discoverable — symlink the repo `skill/` dir into
  `~/.claude/skills/claude-codex-bridge` (symlink, so future `git pull` updates it).
  If `~/.claude/skills/` doesn't exist, create it.
- ADD: a stable scripts location — the skill/AGENTS hooks need to find the bridge
  scripts. Record the absolute repo `scripts/` path where the skill can read it
  (e.g. write `~/.claude-codex-bridge/scripts_path` or have the skill resolve the
  repo from its own symlink target).
- ADD: a clear final summary — "Installed: transport ✓, skill ✓. In ANY project,
  tell your agent: 'set up agent collaboration here' (or run
  `<scripts>/init-collaboration.sh`). Restart Codex to load the MCP server."
- Idempotent; re-runnable; never overwrites an existing skill symlink pointing
  elsewhere without saying so.

## Fix 2 — the skill is the one-phrase trigger

- `skill/SKILL.md` frontmatter `description` is rewritten so Claude auto-invokes it
  on setup phrases: "set up agent collaboration / 装 collab / let Claude and Codex
  work together in this project / start a collaboration board / init collaboration
  here / have two agents collaborate."
- Skill body: when invoked for setup, run `init-collaboration.sh` in the CURRENT
  project (resolve the bridge scripts dir from the skill's install location), which
  creates `<root>/.collab/` + `AGENTS.md`/`CLAUDE.md`, then print the one line the
  user gives the OTHER agent to join (absolute join command). The user never types
  a path or a file name.
- After that, `AGENTS.md`/`CLAUDE.md` exist at the root, so every future agent in
  that project auto-discovers and joins per the membership protocol — the
  one-phrase trigger is only needed once per project.

## The end-to-end UX we want

```
# once per machine
git clone … && cd claude-codex-bridge && ./install.sh      # transport + skill, done
# restart Codex

# once per project, in the project folder, tell either agent:
"set up agent collaboration here"
#   -> skill runs init -> .collab/ + AGENTS.md created -> prints the join line
# from then on, any agent opening that project auto-joins (AGENTS.md/CLAUDE.md)
```

## v1 resolution (per Codex review — REVISE → GO)

Verified on this machine: `~/.codex/skills/<name>/SKILL.md` IS where Codex reads
local skills (e.g. `~/.codex/skills/hyperframes/SKILL.md`), and `~/.claude/skills/`
for Claude. So the hard constraints:

- **Install the skill to BOTH** `~/.claude/skills/claude-codex-bridge` AND
  `~/.codex/skills/claude-codex-bridge` (symlinks to the repo `skill/` dir).
  Without the Codex side, "say the phrase in Codex" can't work — `AGENTS.md` is the
  POST-init discovery mechanism, not the first-time bootstrap. Idempotent; warn (not
  silently overwrite) on an existing non-bridge target.
- **Single source of truth for paths:** `install.sh` writes
  `~/.claude-codex-bridge/scripts_path` (and `repo_path`). The skill body resolves
  the bridge scripts from that file FIRST (model can't reliably know SKILL.md's real
  path), with a fallback to common locations. Re-running `install.sh` fixes a stale
  path after the repo moves.
- **Skill is instructions, not magic.** Body must degrade gracefully: if a shell is
  available, run `"$SCRIPTS/init-collaboration.sh" "$PWD"`; if not, print that exact
  command for the user. Promise "the agent runs it or gives you the one command,"
  never "it auto-happens."
- `install.sh` stays machine-wide; an OPTIONAL `--init-project DIR` may set up one
  project, but install never auto-inits the bridge repo itself.
- Final summary prints: what installed, "restart Codex/Claude to load," and the
  one phrase to use in any project.

## Open questions for Codex
1. Skill install as a SYMLINK (live-updates on git pull) vs COPY (stable, but stale
   after pull)? I lean symlink.
2. How should the skill resolve the bridge `scripts/` dir at runtime — from the
   symlink target of its own install path, or a recorded path file? Most robust?
3. Codex side: is there an equivalent "skill" install for Codex, or is the
   root `AGENTS.md` (auto-read) the entire Codex-side discovery story? (I think
   AGENTS.md is it; Codex has no ~/.claude/skills equivalent we rely on.)
4. Should `install.sh` also offer a `--project DIR` to set up a project in one shot
   (install + init that project), or keep install (machine-wide) and setup
   (per-project, via the phrase) separate? I lean separate.
5. Anything that makes "download → ./install.sh → say one phrase" not actually
   leave a working, discoverable, collaborating setup.
