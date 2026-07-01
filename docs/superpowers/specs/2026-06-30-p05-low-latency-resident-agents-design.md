# P0.5 — Low-Latency Resident Agents (Symmetric Live) — Design Spec

**Date:** 2026-06-30
**Author:** Claude (lead); direction approved by Jack
**Status:** Approved direction (feasibility done) → design for review

## 1. Goal

Make the group chat feel like a **real, low-latency, equal conversation** — Jack's core UX
requirement ("如果反馈慢，那就完全失去了意义"). Concretely: cut Codex's per-reply latency from the
current **~12–16s cold-spawn** toward a few seconds, give agents **conversational context continuity**
(stop the "fresh spawn, no context, guesses" C③ disease), and make the live capability **symmetric** —
either Claude or Codex can be a live, capable lead. Fold in **roles-nominal** (stop hardcoding
codex=implementer / claude=reviewer) as part of the same actor→capability layer.

## 2. Feasibility findings (measured 2026-06-30)

| Mechanism | Latency | Notes |
|---|---|---|
| cold `codex exec` (current) | ~12s | new process + model connection per message — the dominant cost |
| `codex exec resume --last`/`<id>` | ~7s | **also recalls prior context** (verified: recalled "42" across turns) |
| `codex exec-server --listen ws/stdio` | target <few s | a **resident** Codex process; requests over a socket, no per-message cold-spawn; EXPERIMENTAL, protocol undocumented |

Claude's side is already a warm session; its latency is board-wait poll (tuned 5s→1s) + harness
re-invoke + per-turn tool steps (minimized). So the asymmetry — and the win — is on the Codex side.

## 3. Non-goals (deferred)

- Full multi-user / cross-machine hosting of resident agents (single machine for now).
- Replacing the read-only responder architecture wholesale — Phase A improves it in place.
- Sub-second "instant" replies — LLM generation has a floor; the target is "feels live" (a few seconds),
  not zero.

## 4. Architecture — two phases

```
Phase A (ship first): session-resume  → 12s→~7s + context continuity, low risk
Phase B (then):       resident server  → few-second replies, experimental
Both sit behind one actor→capability layer (also fixes roles-nominal).
```

### 4.1 Actor→capability registry (foundation for both phases + roles-nominal)

A single module maps an actor name to HOW it is spawned for each role, so nothing hardcodes
codex/claude and either agent can implement OR review:

```
scripts/_agent_cli.py (new)
  spawn_chat(actor, prompt, project, image_path=None, session=None) -> (reply_text, session_id)
  spawn_implement(actor, prompt, project, image_path=None) -> result
  spawn_review(actor, prompt, project) -> review_text
  # internally: Codex -> codex exec [resume] ...; Claude -> claude -p ... (headless)
```

- `_chat_respond._spawn_codex/_spawn_claude`, `_chat_executor.default_implement/default_review` are
  refactored to call this registry with the RESOLVED role (implementer/reviewer from `_roles_for`),
  not a hardcoded binary. This closes **roles-nominal**: rotate the lead and the right CLI is spawned.

### 4.2 Phase A — session-resume for a warm, context-carrying Codex

- The Codex chat responder keeps a **dedicated chat session id** per project, stored at
  `.collab/.codex_chat_session` (a small JSON: `{"session_id": "..."}`). First reply: fresh
  `codex exec` (capture the new session id); subsequent replies: `codex exec resume <session_id> <prompt>`.
- Capturing the session id: use `codex exec --json` (emits JSONL events including the session id) OR
  read the newest file under `~/.codex/sessions/` created by our run. **Implementation note:** the exact
  capture path is the first thing to nail in implementation (spike it) — prefer `--json` if it carries
  the id; fall back to the sessions-dir mtime.
- Robustness: if resume fails (stale/missing session, error), fall back to a fresh `codex exec` and
  re-capture the id. Never hang (keep `< /dev/null` + timeout). A `--last` variant is NOT used (it can
  pick an unrelated recent session, e.g. an implement task) — always resume the tracked chat id.
- Result: ~7s replies + Codex remembers the conversation (coherence win). This is the immediately
  shippable, low-risk deliverable Jack validates first.

### 4.3 Phase B — resident exec-server for few-second replies

- A **supervisor** (`scripts/bridge-chat-resident.sh` or extend `bridge-chat-execute.sh`) starts one
  `codex exec-server --listen stdio` (or `ws://127.0.0.1:PORT`) per project, kept alive, and feeds it
  board messages, posting replies. No per-message cold-spawn → few-second latency.
- Spike required first: the exec-server request/response protocol is undocumented — a short feasibility
  spike must establish the message format + a warm round-trip time BEFORE committing Phase B's design.
  If the protocol proves too unstable/experimental, Phase A (7s) stands as the shipped state and Phase B
  is revisited.
- Symmetric-live: with the resident host generalized, the same supervisor can host EITHER agent as the
  live lead (Claude via its warm session / board-wait, Codex via the resident server), delivering the
  "两边都能 live" requirement.

## 5. Components

| File | Responsibility |
|---|---|
| `scripts/_agent_cli.py` (new) | actor→capability registry (chat/implement/review spawns); Phase A resume logic |
| `.collab/.codex_chat_session` (runtime) | tracked Codex chat session id (gitignored, local) |
| `scripts/_chat_respond.py` | responder calls `_agent_cli.spawn_chat` with resume session |
| `scripts/_chat_executor.py` | `default_implement/review` call `_agent_cli` with resolved roles (roles-nominal) |
| `scripts/bridge-chat-resident.sh` (new, Phase B) | supervisor hosting `codex exec-server` |

## 6. Error handling / robustness

- Resume failure → fresh spawn + re-capture id (fail-forward, never lose a reply).
- exec-server crash (Phase B) → supervisor restarts it; responder falls back to Phase A resume path.
- All spawns keep `< /dev/null` (no stdin hang) + timeouts.
- Session file corruption → treat as "no session", fresh spawn.

## 7. Testing

- `_agent_cli`: role→spawn argv is correct per actor (Codex→codex exec, Claude→claude -p); resume path
  builds `codex exec resume <id>` when a session file exists, fresh `codex exec` when not; resume failure
  falls back to fresh (monkeypatch subprocess.run — do NOT spawn real codex in unit tests).
- roles-nominal: with roles rotated (Codex=reviewer, Claude=implementer), `default_implement` spawns
  Claude's implement argv and `default_review` spawns Codex's review argv.
- Phase B: an integration spike test (may be marked slow/skippable) that starts exec-server and does one
  warm round-trip, asserting latency < a threshold.

## 8. Phasing (for the plan)

1. **Phase A.1** — `_agent_cli.py` registry + roles-nominal refactor (no latency change yet; tests).
2. **Phase A.2** — Codex chat responder uses tracked-session resume (12s→~7s + context); the session-id
   capture spike is inside this task.
3. **Phase B** — resident exec-server supervisor (gated on the protocol spike succeeding).

Each phase leaves the tree green and is independently reviewable. Jack validates Phase A's felt latency
before Phase B.
