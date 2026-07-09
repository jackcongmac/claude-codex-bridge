# v1.0.0 — AI PR Gate + cross-model resource awareness

The 1.0 milestone. **claude-codex-bridge is now governance-as-product:** let two
*different* AI models (Claude + Codex) implement and review real changes to your
repo, and only ship with a signed, tamper-evident, human-approved PR — plus live
cross-model resource awareness so you're never blocked by one vendor's quota.

## AI PR Gate — `claude-codex-bridge ship`

One command turns a task into a reviewed, signed GitHub PR:

- Branches, implements in a sandbox, runs **your** test command (must pass).
- A **different model** reviews the exact diff.
- Produces an **SSHSIG-signed evidence envelope** bound to the exact diff + test
  run — tamper with any field and verification fails.
- Opens a **real GitHub PR** (never pushes to `main`); a human merges.
- Guards: base-not-moved + patch-id re-verification so the PR renders exactly
  what was reviewed; blocks fake tests (empty/`true` test commands); `--dry-run`
  to preview the whole flow with no side effects.
- **Honest positioning:** on a single host the signature attests key-holder
  accountability + exact-diff/test binding + genuine cross-*model* review +
  human merge — not machine-independent identity. Cryptographic independence is
  cross-machine / multi-user.

## Cross-model resource awareness

- **Live token/quota gauge for BOTH Claude and Codex** in your status line (and a
  `tmux` pane for the Codex side): 5-hour + weekly usage, context %, with color
  warnings (≥60% yellow, ≥80% red).
- **Reliable local Claude 5-hour reset** countdown, computed from your
  transcripts (no credentials, no network) — the status-line payload's own reset
  field is unreliable, so we compute it the way the community usage tools do.
- **Advisory routing** (`bridge_route`): recommends which model should implement
  vs review based on live quota, so you route around whoever's running low.
  Wired into `ship` as a preflight — advisory only, you decide.
- **Honest staleness:** idle readings are dimmed + timestamped, never faked; an
  out-of-range value is shown as unknown rather than a wrong number.

## Group chat, slimmed down

- Killed the token-burn; kept on-demand "chat → execution"; strict judge (no task
  hallucination from context); executor watermark (never reprocesses the backlog).

## Install / upgrade

```
npm install -g @jackcongus/claude-codex-bridge@1.0.0
```

Every change in this release was implemented by one model, independently reviewed
by the other, and pushed through a signed review gate.
