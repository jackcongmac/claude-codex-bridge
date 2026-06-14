# v0.7.1 - Adapter RFC and capability matrix

This is a planning/docs release. It does not add support for any new CLI.

## Highlights

- Adds `docs/adapter-rfc.md`, the adapter RFC requested in issue #10.
- Compares Claude Code, Codex, Gemini CLI, Aider, OpenCode, and Cursor across
  the adapter risks that matter to this bridge: headless prompt support, session
  persistence, machine-readable output, permission controls, cost reporting, MCP
  compatibility, and install complexity.
- Identifies Gemini CLI as the safest first adapter candidate only after a
  probe spike confirms permission controls and the rest of the adapter contract.
- Keeps the README pitch focused on the supported Claude Code + Codex workflow.

## Verification

- `python3 -m unittest tests.test_adapter_rfc_docs -v` passed.
- `python3 -m unittest discover -v` passed 38 tests.
- `python3 -m py_compile tests/test_adapter_rfc_docs.py` passed.

Closes #10.

No files deleted.
