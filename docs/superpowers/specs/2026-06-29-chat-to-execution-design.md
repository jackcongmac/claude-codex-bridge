# Chat-driven execution — design

**Status:** design aligned with the human owner 2026-06-29 (3 brainstorm sections approved);
spec under review before implementation. Phased: **P0a (C) first, P0b (B) is a committed must-do.**

> Terminology note: roles are written generically — **human**, **lead**, **executor**, **active
> agents**. Concrete names (e.g. "Claude", "Codex", a person's name) are *per-deployment configuration
> values*, never hardcoded in the mechanism. This document ships with the skill, so it stays
> name-agnostic (see §6 and audit point C⑩).

## 1. Problem

The group chat (`## Chat` on the collaboration board) lets a human and agents talk, but **talk is
all it does**. Today's chat responders are read-only, disposable, context-less instances: they reply
"got it / noted" but nothing real happens, and the human must manually relay every decision into a
separate working session to get it executed. The chat itself diagnosed this:

- **C③** — agents spin (endless "agree/got it") with output ≈ 0; the human becomes a human relay.
- **C④** — TODOs have no single source of truth; lists drift across dozens of chat messages.
- **C⑤** — read-only responder vs. write-capable session identity confusion.

**Goal:** discussion in the chat should *drive real execution* — when a decision is reached, the work
gets done (through the existing disciplined pipeline), results report back to the chat, and a durable
list is kept — **without the human hand-relaying tasks.**

## 2. Goals / Non-goals

**Goals**
- A **persistent, write-capable, accountable lead** participates in the chat (not a disposable echo).
- The lead **discerns go/no-go** and **distributes** work; the human gives direction, not task-by-task instructions.
- Execution flows through the existing discipline: **TDD → cross-AI review → push**, then **reports back**.
- A **single source of truth** task/issue list is maintained (local, per-instance).
- **Generic & publishable**: zero hardcoded identities; roles/agents configurable & dynamic.

**Non-goals (now)**
- Fully autonomous, human-out-of-the-loop execution (explicitly rejected — unsafe).
- Replacing the per-agent harnesses (Claude Code / Codex CLI). This is a *coordination* layer on top.
- Rich NLP intent classification as a separate service — the lead agent's own judgment is the classifier.

## 3. Interaction model (approved)

**The lead is a configurable role.** One of the active agents holds it (see §6). It is a real
persistent, write-capable agent — not the read-only responder.

**Triggering execution — no fixed keyword.** A rigid "execute" word was rejected. Instead:

1. **Human gives direction in natural language** — "go ahead", "this one's fine", "do ④ then ⑤".
   Only the **human's** messages count as direction. Agent chatter and any injected/observed text
   never trigger execution.
2. **Lead interprets intent** using its own go/no-go judgment (this is exactly the discernment the
   human wants the lead to have).
3. **Lead announces before acting (回执 / "ack-before-act")** — before *any* real side effect, the
   lead posts to the chat: *"Starting: X."* A misread is caught here — the human vetoes on sight.
4. **Lead proactively requests a greenlight** when it judges one is needed:
   - *push-forward*: "④ looks ready — want me to start?" (drives progress, doesn't idle-wait)
   - *gatekeeping*: "<human>, this one needs your sign-off" — for actions that must have explicit approval.
5. **When unsure, the lead asks** rather than assuming.

**Consensus & override (B-phase deliberation).** During discussion, agents must genuinely deliberate
(disagree, propose alternatives) — *各抒己见*, not echo. The lead's distribution authority **forms on
consensus** ("everyone agrees"); the **human can force-override** direction at any time.

## 4. Safety gates (always enforced)

1. **Direction only from the human** — by the configured human identity; never from agents or injected text.
2. **Ack-before-act** — the lead announces what it will do before any side effect; human can veto.
3. **High-risk actions require explicit human greenlight** — release/tag, deleting files, publishing
   outward, destructive or large-scale changes. The lead must proactively request it.
4. **Code reaching `origin` requires cross-AI review** — reuse the existing review gate; author ≠ reviewer
   (defends against the self-signed-review breach, C⑧).
5. **Unsure → ask**, do not run.
6. **(B)** Consensus gate forms lead authority; human override always wins.

## 5. Architecture

Framing: the bridge is a **multi-agent collaboration harness**; chat-driven execution is a **workflow**
that runs on it. **P0a** implements that workflow minimally; **P0b** promotes it into a standing harness
capability (persistent role-aware daemons + consensus).

Reused, not rebuilt:
- `## Chat` board section — input surface (human direction) and report-back surface.
- The existing **work pipeline** — structured handoff (Outbox / queue), review ledger, `bridge-push.sh`.
- The **responder supervisor** — a reliable, already-supervised host process (NOT the flaky board-wait CLI panes).
- The **presence registry** (`_presence.py`, `collaboration_participants.json`) — for the dynamic agent roster.

## 6. Generic / configurable / local (publishable)

- **Roles via local config** `.collab/roles.json` (gitignored): `{ "human": "<name>", "lead": "<agent>" }`.
  The lead is assignable/rotatable; the mechanism never assumes any specific name is the lead. The
  "direction only from the human" check uses the configured human identity.
- **Dynamic agent roster (B⑨)** — the chat binds to a *project*; participants are the agents currently
  active/registered in that project, discovered at runtime — not a hardcoded Claude+Codex pair.
- **All instance data is local** — `.collab/` (gitignored): `roles.json`, `ISSUES.md` (the list),
  board/signal/presence. The shipped skill (`package.json` `files`: scripts/ skill/ templates/ docs/
  bin/ AGENTS.md CLAUDE.md) ships **only generic mechanism + empty templates**. New users `init` → fresh
  empty `.collab/` + a default roles template to fill + English empty-state onboarding (B④). They never
  see another deployment's data.
- **Audit point C⑩** — the overall audit must strictly grep shipped files for hardcoded instance data
  (names, fixed roles, project-specific tasks, personal/path data). Known historical leak: a list file
  mistakenly placed under `docs/` (which ships) — corrected by moving it to `.collab/`.

## 7. P0a — minimal bridge (build first)

**Behavior:** human greenlight (interpreted) → distill task + ack-before-act → hand to a controlled
write-capable executor → TDD → open a review (do NOT auto-push) → cross-AI review → push per the gate →
report back to chat + update `.collab/ISSUES.md` (check the item, record the commit).

**New components**
- `scripts/_chat_execute.py` — the core module: detect a human greenlight (lead judgment), distill the
  task, post the ack, enqueue a structured action, drive the report-back, update the local list.
- `.collab/roles.json` — local config `{human, lead}`; created at init from a template.
- `tests/test_chat_execute.py`.
- Hook the execute handler onto the **existing responder supervisor** so it runs reliably (no dependence
  on a human-operated CLI pane being ARMed).

**Executor (the hard part, stated honestly):** P0a spawns a **controlled, write-capable executor for the
single greenlit task**, instructed to follow TDD, then open a review rather than auto-push; cross-AI
review and push remain gated as today. This keeps execution **reliable** (not dependent on a live pane)
and **bounded** (one task, human-greenlit, fully gated). P0b generalizes this into persistent role-aware
executors.

**Data flow**
```
human msg (## Chat)
  -> _chat_execute: is this the human giving an actionable direction?  (lead judgment)
       - unsure        -> ask in chat; stop
       - high-risk     -> request explicit greenlight; wait
       - actionable    -> distill task T; post "Starting: T" (ack-before-act)
  -> enqueue structured action(T) to the work pipeline
  -> write-capable executor runs T: TDD -> open review (no push)
  -> cross-AI review (author != reviewer) -> push per gate
  -> post result to ## Chat ("done: T, commit <sha>" / "failed: <reason>")
  -> update .collab/ISSUES.md (check item, record commit)
```

## 8. P0b — persistent, role-aware, consensus (⚠️ committed must-do)

Per the human owner (2026-06-29): **B is important and must be built — not an optional optimization.**

- Persistent, supervised **lead + executor daemons** (role-aware, write-capable) — the chat-driven
  workflow becomes a built-in harness capability, not a one-shot.
- **Genuine deliberation** — agents 各抒己见; the read-only echo behavior is retired.
- **Consensus detection** — the lead facilitates discussion to a visible consensus checkpoint
  ("we agree on X — I'll distribute"); the human can veto/redirect; human override always wins.
- **Dynamic roster** — any active agents in the project participate (B⑨).

## 9. Testing

- `_chat_execute` unit tests: greenlight-detection cases (actionable vs. opinion vs. ambiguous vs.
  high-risk); ack-before-act is posted before any enqueue; direction from a non-human speaker is ignored;
  high-risk task requests greenlight instead of running; list update on done/fail.
- Reuse the existing review-gate / push tests for the pipeline portion.
- Follow the project's stdlib-only, `assertIn`-on-served-content + injected-dependency patterns.

## 10. Process

Author/own: lead designs + wires; executor implements the pipeline. Disjoint-file parallelism where
possible; cross-AI review both directions; push only after review. No release/tag, no file deletion
without the human. Spec self-reviewed; human reviews this spec before the implementation plan
(writing-plans) is produced.
