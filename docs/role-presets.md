# Role Presets

Role presets are small JSON files that copy known-good role and routing
preferences into `collaboration_state.json`.

The JSON files in [`../presets`](../presets) are the canonical source. The docs
explain when to use them, but the preset files own the exact `roles` and
`resource_profiles` values. This keeps the presets aligned with the state fields
that `_auto_turn.py`, dashboards, and tests already read.

## Apply a Preset

From the bridge repo:

```bash
scripts/apply-role-preset.py --project /path/to/project --preset max-claude-pro-codex
```

The command updates `collaboration_state.json` in place. It preserves unrelated
state such as `status`, `turn`, `max_turns`, `max_cost_usd`, and `last_writer`.
Running the same preset more than once is idempotent.

Apply presets while the loop is paused. By default the command refuses to write
when `status` is `active` or `collaboration.lock` exists. Use `--force` only
when you intentionally want to override that guard.

## Preset: max-claude-pro-codex

Use this when Claude has the deeper or more expensive reasoning budget and Codex
is the primary executor. It sets Claude as `reviewer`, Codex as `executor`, and
adds resource profiles for the common Claude Max + Codex Pro split.

Routing intent:

- Claude: architecture, ambiguity resolution, test strategy, large-context
  review, final QA.
- Codex: implementation, search, small fixes, test iteration, mechanical docs
  updates.
- Human: scope, permissions, security posture, cost caps, and product judgment.

## Preset: reviewer-implementer

Use this when you want the role split without subscription-specific resource
profiles. It sets Claude as `reviewer` and Codex as `executor`, leaving any
existing `resource_profiles` untouched.

This preset is intentionally two-agent only. Multi-CLI support remains a
separate RFC track.

## Preset Schema

Presets reuse the same field names as `collaboration_state.json`:

- `roles`: required; exactly `Claude` and `Codex`.
- `resource_profiles`: optional; exactly `Claude` and `Codex` when present.
- `routing_rules`: optional human-readable notes for docs and reviews.

Preset fields do not grant permissions. Write access still comes from watcher
flags and role permission narrowing.
