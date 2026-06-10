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

## Billing mode (orthogonal axis — DON'T skip auto-reload)

Tier is *capacity*; billing *mode* decides whether hitting a limit is a hard stop.

| Billing mode | What happens at the limit | Natural circuit breaker | Bridge implication |
| --- | --- | --- | --- |
| Subscription (Max/Pro) | rate-limit / quota pauses you | the provider's rate limit | handoff/coverer may trigger; caps are backup |
| API, hard cap (no reload) | balance hits 0 → stops | the balance | cost-exhaustion ends the loop |
| **API + credit-card AUTO-RELOAD** | **card tops up → the agent KEEPS RUNNING** | **NONE — removed by auto-reload** | ⚠️ the bridge's `max_cost`/`max_turns` are the ONLY brake; an unattended loop can spend without bound |

**Auto-reload is the dangerous case for autonomous mode.** With no provider-side
stop, `max_cost_usd` + `max_turns` are the sole guard against runaway spend. So:
- For auto-reload users, set caps CONSERVATIVELY and treat them as hard safety,
  not hints.
- The **Codex side has no parseable cost**, so it is governed by `max_turns`
  ONLY — an auto-reload Codex user has no `$` guard at all; the turn cap is the
  entire brake. Flag this loudly.
- This reinforces the Slice 4 improver **safety floor**: the improver must NEVER
  raise `max_cost`/`max_turns` — for an auto-reload user that is removing the brakes.
- The coverer/handoff is NOT the mechanism here (no hard limit to hand off from);
  the loop simply halts on the caps.

Suggest a `resource_profiles[X].billing` field (`subscription` | `api_hard_cap` |
`api_auto_reload`) so the dashboard can warn when auto-reload + autonomous mode are
combined.

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
