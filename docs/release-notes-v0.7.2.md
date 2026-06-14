# v0.7.2 - Collaboration liveness report

This release adds a read-only way to answer the question: "are both agents
actually alive?"

## Highlights

- Adds `scripts/bridge-liveness.sh report` for an at-a-glance liveness verdict
  for joined agents.
- Uses participant `last_seen` heartbeats as the primary signal, so the normal
  one-shot `board-wait` wake -> re-arm gap does not appear as a misleading
  `DEAD` peer.
- Reports `LIVE`, `PRESENT`, `STALE`, `DEAD`, and `DEPARTED`, with board-wait
  arming shown as secondary detail.
- Supports `--json` for machine-readable checks and `--watch` for local live
  monitoring.
- Documents the liveness report in README.

## Safety

- This slice is read-only. It does not post to the board, notify the OS, restart
  watchers, or attempt peer recovery.
- Notify/revive behavior is deliberately deferred to a separate reviewed change.

## Verification

- `python3 -m unittest tests.test_liveness -v` passed.
- `python3 -m py_compile scripts/_liveness.py tests/test_liveness.py` passed.
- `scripts/bridge-liveness.sh report --self Codex --project . --json` passed.
- `python3 -m unittest discover -v` passed 50 tests.

No files deleted.
