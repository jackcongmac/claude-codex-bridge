# DRAFT (Claude → Codex handoff): full subscription/resource pairings

Hand-off for Codex to integrate into `docs/resource-aware-routing.md`, then delete
this draft. `resource-aware-routing.md` currently documents only ONE preset
(`max-claude-pro-codex`). Jack asked for the full set of pairings. Proposed matrix:

## Pairing matrix (routing changes per combination)

| # | Pairing | Who is scarce / binding constraint | Routing strategy | Likely handoff (coverer) direction |
| --- | --- | --- | --- | --- |
| 1 | **Claude Max + Codex Pro** (current preset) | Claude reasoning is the high-value scarce resource | Claude = planner/strict reviewer; Codex = executor | Codex more likely to hit turn caps → Claude covers |
| 2 | **Claude Pro + Codex Pro** (both bounded) | BOTH quotas | near-symmetric, but tighten `max_turns`/`max_cost`; avoid long loops | either side; watch both budgets |
| 3 | **Claude Max + Codex Plus/Free** (Codex weak) | Codex quota | Codex only cheap mechanical work; Claude does more synthesis | Codex limited → **Claude covers** |
| 4 | **Claude Pro/Free + Codex Pro** (Claude weak) | Claude quota | **roles reverse**: Codex primary executor+reasoner, Claude = scarce reviewer only | Claude limited → **Codex covers** |
| 5 | **Both Max / high tier** (abundant) | neither (cost may still matter) | longer loops OK; higher caps; more parallel exploration | rare; budget caps still bound |
| 6 | **One side via API (pay-per-token)** | the API side's $ | API side governed by `max_cost`; subscription side by quota/turns | cost-exhaustion → cover with the subscription side |
| 7 | **Asymmetric context windows** (e.g. Claude 1M vs Codex) | context capacity | route large-context synthesis to the bigger-window agent regardless of tier | `context_full` handoff from the smaller-window side |
| 8 | **Trust/permission asymmetry** | write authority | only one side gets `--allow-write`; the other reviews | n/a (permission, not limit) |

## Tie-in with the coverer (Slice 3)

Resource profiles should *inform* the handoff/coverer feature: `resource_profiles[X].tier`
plus current quota pressure predicts **who is likely to be limited and who should
cover**. Scenarios 3/4/6/7 are exactly the graceful-handoff triggers. Suggest a
short "Resource profiles drive coverage" subsection: the limited side self-reports
`handoff:{reason}`, the abundant side covers (bounded by `max_cover_turns`).

## Presets to add (alongside `max-claude-pro-codex`)

- `balanced-pro-pro` (#2), `reversed-pro-codex` (#4), `abundant-both-max` (#5),
  `api-cost-bound` (#6), `big-context-claude` (#7). Each just sets `roles` +
  `resource_profiles` + caps; none grant permissions.

Honesty note to preserve: Codex `exec` cost isn't reliably parseable, so for any
pairing the Codex side is governed by `max_turns`, not `max_cost`.
