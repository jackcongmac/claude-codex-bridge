# DESIGN: pre-collaboration handshake + visible confirmation — v1

Status: **DRAFT v1 — for Codex review (GO/REVISE).** Fixes a real UX failure Jack
hit repeatedly: agents are told to "just contact each other," one side isn't
actually ready (not restarted, not joined, board-wait not ARMed), and the call /
board post hangs in awkward silence ("就尬在那里") with no diagnosis. The user can't
tell working-from-hung, loses trust, and cancels.

## The failure mode, precisely

Real collaboration between two RUNNING windows happens over the board: an agent
writes + bumps the signal, the peer's backgrounded `board-wait` wakes it. The whole
thing depends on the peer having **ARMed board-wait**. If the peer window never
ARMed (or never joined, or Codex wasn't restarted so the transport is dead), then:

- a board post gets **no reaction** — silent, indefinitely;
- a blocking `mcp__codex__codex` call shows `Calling…` and hangs.

Either way: no signal to the user that something is *wrong* vs merely slow. That is
the bug.

## The fix: a handshake that NEVER hangs + a confirmation the user can trust

Before any real (slow, blocking, open-ended) collaboration, run a **fast preflight
handshake** that proves the channel is live end-to-end, with a HARD timeout so it
fails fast instead of hanging. On success, print a short, friendly **confirmation
block** so Jack can relax ("现实确认信息"). On failure, print the **exact remediation
command** instead of silence.

### Two halves

**1. Static preflight (local, instant, never blocks).** Tick each, ✓/✗:
- Board found at `<root>/.collab` (via `find_project_root`); signal + participants readable.
- I (initiator) am joined and my own `board-wait` is ARMed (my pidfile exists & pid alive).
- Peer is in `participants.json`, `departed:false`, and `last_seen` is FRESH
  (within `BRIDGE_PRESENCE_STALE`, default 1800s). Stale last_seen ⇒ warn loudly.
- (optional `--check-transport`) transport wired: Claude side `claude mcp get codex`
  succeeds; Codex side `[mcp_servers.claude_chat]` present in `~/.codex/config.toml`.

**2. Live liveness ping (the real handshake) — harness-to-harness, deterministic.**
This is the part that directly tests "is the peer's board-wait actually ARMed?"

- A small `collaboration_handshake.json` channel:
  `{ "ping": {"from","nonce","at"}, "pong": {"from","nonce","at"} }`.
- Initiator (`bridge-handshake.sh`) writes `ping{from=self, nonce, at}` atomically,
  bumps the signal with `changed_section="HANDSHAKE"`, then **polls** `handshake.json`
  for `pong.nonce == ping.nonce` up to `--timeout` (default 15s).
- The PONG is written **by the peer's `board-wait.sh` itself**, not by the peer
  agent. In its poll loop, when `board-wait` sees an unanswered handshake ping whose
  `from != self`, it writes `pong{from=self, nonce}` atomically and **keeps waiting**
  — it does NOT exit/wake its agent (a liveness probe is not real work). So:
  - PONG within timeout ⇒ peer's board-wait is alive & ARMed ⇒ **GO**.
  - No PONG ⇒ peer isn't ARMed (or window closed) ⇒ **NO-GO** with the exact fix.

Doing the ACK at the **harness layer** (board-wait), not the agent layer, is the
whole point: it proves the *mechanism* that real collaboration depends on is live,
deterministically, without relying on the peer agent to "remember to respond" —
which is exactly the thing that was failing.

### A handshake ping must NOT spuriously wake the peer agent

`board-wait` currently exits CHANGED on any signal bump (peer + update_id changed).
New rule: if the only change is a handshake ping (changed_section=="HANDSHAKE" and
the ping is the sole new content), board-wait writes the pong and **continues
waiting** rather than exiting. Real board changes still exit CHANGED as before. This
keeps handshakes cheap and invisible to the peer's turn loop.

## Confirmation block (GO) — what Jack sees

```
✅ 握手成功 — 可以放心开始协作
   Peer:       Codex (role=peer) — 在线，board-wait 已 ARM
   Transport:  codex MCP ✓        claude_chat ✓
   Board:      /Volumes/WorkHD/DWJX/.collab   (signal update_id=14)
   Round-trip: 1.2s
   两个 agent 现在都在监听同一块板。开始。
```

## NO-GO block — diagnosis + exact fix, never silence

```
❌ 握手失败 — 先别开始，差一步（这不是卡住，是对面没就绪）
   Peer Codex 在 15s 内没有回应握手 ping。
   最可能：Codex 窗口没有 ARM board-wait（它不会自动对板更新做反应）。
   修复 — 在 Codex 窗口里运行：
     <scripts>/join-collaboration.sh --self Codex --role peer
     <scripts>/board-wait.sh --self Codex --project <root> &
   修好后重跑：<scripts>/bridge-handshake.sh --self Claude --peer Codex
```

Each ✗ above the ping maps to its own remediation line (board missing → init; I'm
not ARMed → ARM myself; peer departed/stale → re-join; transport down → re-run
install + restart Codex).

## When the handshake runs (protocol integration)

- **First contact of a session:** before the first real peer interaction, the
  initiator runs `bridge-handshake.sh`. GO ⇒ proceed; NO-GO ⇒ show fix, don't post
  the big task into the void.
- The proactive-offer / setup flow ends by running a handshake so "set up
  collaboration here" finishes with a *confirmed-live* channel, not a hopeful one.
- `join-collaboration.sh` can end by ARMing then printing "run a handshake to
  confirm the peer is live."
- Make it a non-negotiable (#7): **handshake before you hand off.** Don't dump a
  task into the board/peer-call until a handshake (or a fresh peer reaction this
  session) confirms the other side is live.

## Files touched

- NEW `scripts/bridge-handshake.sh` — initiator: static preflight + ping + poll +
  GO/NO-GO + confirmation. Hard timeout; exit 0 on GO, non-zero on NO-GO.
- `scripts/board-wait.sh` — auto-ACK handshake pings in the poll loop (harness-layer
  pong); don't exit on a handshake-only change.
- `scripts/bridge_common.py` — add `handshake` path to `collab_paths`; tiny
  read/write-pong helper used by both scripts (atomic, nonce-checked).
- docs/protocol + AGENTS.md/CLAUDE.md — non-negotiable #7; skill setup flow ends
  with a handshake + confirmation.
- tests — pong written by a live board-wait ⇒ GO; no armer ⇒ NO-GO within timeout;
  handshake ping does NOT cause board-wait to exit CHANGED; stale-peer warning.

## v2 resolution (per Codex review — REVISE → GO-worthy, now implemented)

Codex's REVISE was adopted in full; it improved the design:

- **Q1 harness-layer pong:** confirmed. The pong is written by the peer's
  `board-wait.sh`, never the agent — it's the only layer that proves the ARM
  mechanism is alive.
- **Q2/Q3 fully decouple from the signal:** a SEPARATE
  `collaboration_handshake.json`; handshake NEVER bumps `collaboration_signal.json`'s
  `update_id`. `board-wait` ACKs any ping addressed to `self` at the TOP of its loop
  (before the signal check), then handles real signal changes as before — so a ping
  can never mask/steal a real board update, and the "don't-exit-on-handshake-change"
  hack is gone entirely (the channels are simply separate). Records carry
  `from`/`to`/`nonce`/`created_at`/`expires_at`; read-modify-write is guarded by a
  small dedicated `collaboration_handshake.lock`, not the main lock.
- **Q4 timeout:** default `max(15, 3*interval+1)`; the live-ping line prints "waiting
  up to Ns…" immediately so it never feels silent.
- **Q5 ARM order:** remediation says explicitly "ARM in BOTH windows, then re-run";
  join does NOT auto-ARM (kept optional/future).
- **Q6 hang safeguards:** static checks print before polling; every NO-GO exits
  nonzero within the timeout; corrupt/stale handshake JSON is ignored with the
  channel rewritten; pings are addressed to a SPECIFIC peer; expired pings are not
  ponged.

Verified end-to-end: a live ARMed peer board-wait → GO (rtt ~0.5s); no peer armer →
NO-GO within timeout with the exact fix; a handshake ping does NOT wake/exit the
peer's board-wait (it keeps waiting).

## Open questions for Codex

1. **Pong at the harness layer (board-wait) vs agent layer.** I argue harness —
   it proves the ARM mechanism deterministically and can't be forgotten. Agree?
2. **Separate `collaboration_handshake.json` vs reusing the signal.** I lean a
   separate small file so a handshake never perturbs `update_id` semantics that
   real reactions key off. Or fold ping/pong into the signal with a `handshake`
   field? Concern: not double-waking on the pong.
3. **board-wait "don't exit on handshake-only change":** is keying off
   `changed_section=="HANDSHAKE"` + nonce robust enough, or do we need an explicit
   ping/pong epoch to avoid a ping racing a real change in the same bump?
4. **Default timeout 15s** for the live ping — long enough for a 5s-interval
   board-wait to catch + ACK, short enough not to feel like a hang. OK? Should it
   scale with the peer's `--interval`?
5. **Who ARMs first / chicken-and-egg:** if NEITHER side is ARMed, the handshake
   correctly NO-GOs, but then both must ARM before it can pass. Is the remediation
   ("ARM both, then re-run") clear enough, or should join auto-ARM?
6. Anything that makes "handshake before handoff" still able to hang or still leave
   the user staring at silence.
