# Resource-Aware Routing

`claude-codex-bridge` should not treat Claude Code and Codex as symmetric
round-robin workers. Users often have asymmetric subscriptions, quota pressure,
context windows, and trust boundaries. A common case is:

- **Claude Max**: scarce but high-leverage reasoning capacity.
- **Codex Pro**: strong execution capacity for bounded coding loops.

The bridge should help users route work by agent strengths and subscription
constraints, not by turn symmetry.

## Pairing Matrix

Use the pair of agents you actually have, not the idealized one. Tier determines
capacity; context window, permissions, and billing mode determine what can run
unattended.

| # | Pairing | Binding constraint | Routing strategy | Likely cover direction |
| --- | --- | --- | --- | --- |
| 1 | **Claude Max + Codex Pro** | Claude reasoning is valuable and should be reserved | Claude plans and reviews; Codex executes bounded edits | Codex may hit turn caps, so Claude covers judgment-heavy wrap-up |
| 2 | **Claude Pro + Codex Pro** | both quotas | near-symmetric, with tighter `max_turns` and shorter loops | either side can cover; watch both budgets |
| 3 | **Claude Max + Codex Plus/Free** | Codex quota | Codex handles cheap mechanical work; Claude does more synthesis | Codex limited, so Claude covers |
| 4 | **Claude Pro/Free + Codex Pro** | Claude quota | roles reverse: Codex is primary executor/reasoner; Claude is scarce reviewer | Claude limited, so Codex covers |
| 5 | **Both Max / high tier** | neither tier is immediately scarce | longer loops and broader exploration are reasonable, but caps still apply | rare; budget caps still decide |
| 6 | **One side via API** | the API side's spend | API side is governed by `max_cost`; subscription side by quota/turns | cost exhaustion hands off to the subscription side |
| 7 | **Asymmetric context windows** | context capacity | route large-context synthesis to the larger-window agent regardless of tier | smaller-window side hands off on `context_full` |
| 8 | **Trust / permission asymmetry** | write authority | only one side gets project write permission; the other reviews | no coverer; this is a permission boundary |

The important reversal is row 4: if Claude is the scarce side and Codex has the
better budget, Codex should own more reasoning and execution, with Claude used
for focused review only.

## Billing Modes

Billing mode is orthogonal to tier. A high tier can still need strict local caps
if the provider will keep charging.

| Billing mode | At the limit | Natural circuit breaker | Bridge implication |
| --- | --- | --- | --- |
| Subscription | provider rate limit or quota pause | the provider stops new work | handoff may trigger; local caps are backup |
| API hard cap | balance reaches zero | the balance | cost exhaustion ends the loop |
| **API + credit-card auto-reload** | card tops up and work continues | none | `max_cost_usd` and `max_turns` are the brake |

`API + credit-card auto-reload` is the dangerous autonomous-mode case. With no
provider-side stop, an unattended loop can keep spending unless local caps halt
it. For auto-reload users:

- Set `max_cost_usd` and `max_turns` conservatively.
- Treat caps as hard safety boundaries, not suggestions.
- Never let an improver role raise caps automatically.
- Remember that the **Codex side has no parseable cost** today, so Codex is
  governed by `max_turns`, not `max_cost_usd`.

Future dashboards can model this with a `resource_profiles[*].billing` value:
`subscription`, `api_hard_cap`, or `api_auto_reload`.

## Preset: max-claude-pro-codex

Use this preset when Claude has the deeper or more expensive reasoning budget
and Codex is the primary executor.

| Actor | Role | Best use |
| --- | --- | --- |
| Human | final decision maker | scope, taste, risk, budget, approvals |
| Claude Max | planner / strict reviewer | architecture, ambiguity resolution, test strategy, large-context review, final QA |
| Codex Pro | executor / integrator | implementation, search, small fixes, test iteration, mechanical docs updates |

The practical goal is to keep Claude focused on judgment and review while Codex
handles the repetitive execution loop.

## Preset Candidates

The shipped preset is intentionally conservative, but the matrix above maps to
additional useful presets:

| Preset | Scenario | Intent |
| --- | --- | --- |
| `balanced-pro-pro` | Claude Pro + Codex Pro | symmetric roles with lower caps |
| `reversed-pro-codex` | Claude Pro/Free + Codex Pro | Codex as primary executor/reasoner; Claude as scarce reviewer |
| `abundant-both-max` | both high tier | broader exploration with explicit caps |
| `api-cost-bound` | one side is API billed | route by spend and halt hard on cost caps |
| `big-context-claude` | Claude has the larger context window | send large synthesis and review to Claude |

Presets change roles, `resource_profiles`, and caps. They must not grant write
permissions; write authority stays with watcher flags and the harness.

## Escalation Rules

### Escalate to Claude

Codex should escalate to Claude when the next step needs:

- architecture or API boundary judgment
- large-context synthesis across many files
- adversarial review of a diff or plan
- test strategy or risk assessment
- final QA before release
- a decision where a wrong answer would create expensive rework

### Hand back to Codex

Claude should hand back to Codex when the next step is:

- a bounded implementation task
- mechanical file edits
- repository search
- running or iterating on tests
- applying a specific review finding
- updating docs from an already-approved decision

### Ask the human

Either agent should ask the human when the next step changes:

- product scope
- user-facing behavior
- security posture
- local write/shell permissions
- expected cost or quota consumption
- taste, narrative, or business judgment

## State Template

The default `templates/collaboration_state.json` includes `resource_profiles`:

```json
{
  "resource_profiles": {
    "Claude": {
      "tier": "max",
      "best_for": ["architecture", "large_context_review", "ambiguity_resolution", "test_strategy", "final_qa"],
      "avoid": ["bulk_editing", "repetitive_search", "mechanical_file_updates"]
    },
    "Codex": {
      "tier": "pro",
      "best_for": ["implementation", "search", "small_fixes", "test_iteration", "mechanical_docs_updates"],
      "avoid": ["large_context_synthesis", "final_architecture_decisions", "unreviewed_security_judgment"]
    }
  }
}
```

These profiles are guidance for prompts, templates, and dashboards. They do not
grant permissions. Actual write authority still comes from the watcher flags and
role permission narrowing in the harness.

## Resource Profiles Drive Coverage

Resource profiles should inform handoff and coverer behavior. The limited side
self-reports a handoff reason such as `quota_pressure`, `context_full`,
`cost_cap_near`, or `permission_boundary`; the more suitable side covers the next
bounded step.

Examples:

- A Codex Plus/Free executor hits turn pressure and hands final QA to Claude Max.
- A Claude Pro reviewer keeps review comments short and hands implementation
  back to Codex Pro.
- A small-context side detects `context_full` and asks the larger-context side to
  synthesize before continuing.

Coverage still remains bounded by `max_cover_turns`, `max_turns`, and any cost
cap. Handoff is a safety valve, not permission escalation.

## Dashboard Implications

A resource-aware dashboard should make the routing visible:

- how many turns each agent took
- which role each agent held
- whether Claude turns were used for high-leverage work
- whether Codex turns were used for bounded execution
- whether the loop halted because of turn or cost caps

Do not overclaim complete cost accounting. Today Claude cost can be parsed from
Claude's JSON output, while Codex cost is governed primarily through turn caps
because `codex exec` cost is not reliably available.
