# P0.5 Phase B — exec-server / app-server resident spike — Findings

**Date:** 2026-06-30 (autonomous)
**By:** Claude (lead)
**Verdict: FEASIBLE but DEFER.** Phase A (`codex exec resume`, ~7s + context) stands as the shipped
low-latency state. Phase B (a resident Codex server) is buildable but is a substantial, EXPERIMENTAL
JSON-RPC client effort whose marginal latency gain over Phase A is uncertain — not the best use of the
current autonomous window vs. the concrete UX/coherence backlog.

## What was investigated

- `codex exec-server --listen ws://IP:PORT | stdio` — a standalone resident exec service exists.
- `codex app-server generate-json-schema --out DIR` — the protocol IS documented: it emits `v1/` + `v2/`
  (230 method schemas), `ServerRequest.json`, `ServerNotification.json`. The surface is a full JSON-RPC
  lifecycle: `thread`/`threadId` create+resume, `turn`/`turnId` send-a-prompt, `TurnCompletedNotification`,
  plus `execCommandApproval` / `PermissionsRequestApproval` (approval round-trips), interrupts, diffs.
- `codex mcp-server` + the `codex` / `codex-reply` MCP tools are an already-working persistent channel
  (a resident supervisor could be an MCP client and call `codex-reply` warm).

## Why DEFER (not NO-GO)

- **Feasible:** the protocol is documented (generate-json-schema) and there are two viable resident
  routes — (a) an app-server/exec-server JSON-RPC client, or (b) a supervisor acting as an MCP client
  using `codex-reply` on a resident `codex mcp-server`.
- **But costly + experimental:** route (a) means implementing thread/turn/approval/notification handling
  against an EXPERIMENTAL protocol that may change between codex versions. Route (b) is simpler but still
  a resident MCP-client supervisor with lifecycle/restart handling.
- **Uncertain marginal gain:** Phase A already went 12–16s → ~7s. The residual is largely model-inference
  time (a few seconds floor regardless of transport), so Phase B likely buys ~7s → ~3–4s, not instant.
- **Opportunity cost:** the autonomous window is better spent finishing concrete backlog (top project bar,
  compact-on-close, connectivity tests, chat→execution end-to-end validation, docs) that users feel now.

## Recommended future path (when Phase B is picked up)

1. Prefer **route (b): resident `codex mcp-server` + a supervisor MCP client** calling `codex-reply` — it
   reuses the documented, stable MCP protocol and the existing `.codex_chat_session`/thread concept.
2. Spike a real warm round-trip latency measurement first; only build the resident supervisor if warm
   replies are materially < Phase A's ~7s.
3. Generalize the resident host so EITHER agent (Claude/Codex) can be the live lead (the symmetric-live
   goal), reusing `_agent_cli.py`'s actor→capability layer.

## Status

Phase A shipped (`7a30c89`). Phase B: documented + deferred (this note). No code shipped for Phase B.
