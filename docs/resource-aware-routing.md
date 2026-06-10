# Resource-Aware Routing

`claude-codex-bridge` should not treat Claude Code and Codex as symmetric
round-robin workers. Users often have asymmetric subscriptions, quota pressure,
context windows, and trust boundaries. A common case is:

- **Claude Max**: scarce but high-leverage reasoning capacity.
- **Codex Pro**: strong execution capacity for bounded coding loops.

The bridge should help users route work by agent strengths and subscription
constraints, not by turn symmetry.

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
