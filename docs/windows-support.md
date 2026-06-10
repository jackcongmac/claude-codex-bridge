# Windows Support Notes

Status: native Windows support is not yet verified for this repository. Do not treat Windows as supported until the items below are tested on a real Windows machine.

The current supported path remains macOS and Linux. Windows users should treat
this page as a compatibility note and implementation plan, not a guarantee.

## CLI Availability

Before testing the bridge, verify both CLIs in the exact shell you plan to use:

```powershell
claude --version
codex --version
python --version
```

If Claude or Codex is unavailable in native PowerShell, try WSL or Git Bash and
run the same checks there. Do not assume a CLI installed in one environment is
visible in another.

## Config Paths

The current installer is a Bash script and uses `$HOME` unless `CODEX_HOME` is
set. That means the effective config path depends on the shell:

| Environment | Likely Codex config path |
| --- | --- |
| WSL | `~/.codex/config.toml` inside the Linux distro |
| Git Bash | `$HOME/.codex/config.toml` as seen by Git Bash |
| Native PowerShell | `%USERPROFILE%\.codex\config.toml` |

For Claude Code registration, verify the native CLI's own MCP config location
with `claude mcp get codex` after installation. Do not copy a WSL config path
into native Windows unless both CLIs are running in the same environment.

## Shell Assumptions

`install.sh`, `scripts/init-collaboration.sh`, and
`scripts/watch-collaboration.sh` are Bash scripts. They are not PowerShell
scripts.

Current options:

- Use WSL and run the bridge entirely inside WSL.
- Use Git Bash if both `claude`, `codex`, and `python3` are visible there.
- Add a future `install.ps1` for native PowerShell.

A native PowerShell installer would need to:

- locate `python`, `claude`, and `codex`
- write `%USERPROFILE%\.codex\config.toml`
- preserve existing config blocks
- register `codex mcp-server` through `claude mcp add`
- support the read-only `CLAUDE_CHAT_ALLOWED_TOOLS` flow

## Watcher Behavior And File Locking

The autonomous watcher uses Python for the actual turn commit path and a
project-local `collaboration.lock` file for file locking. The lock is created
with exclusive file creation in `_auto_turn.py`, which should map to Windows
filesystem semantics, but this has not yet been verified in this repo.

The wrapper shell script is still Bash-only. A Windows-native watcher should be
tested separately, especially for:

- lock creation and stale lock cleanup
- path handling with backslashes and spaces
- long-running `claude -p` and `codex exec` child processes
- Ctrl-C behavior
- log file writes to `collaboration_auto.log`

Notifications are best-effort. The current code uses `osascript` on macOS and
`notify-send` on Linux. Windows does not currently have a notification backend;
the bridge should still write state and log files, but desktop notifications
should be treated as unavailable until implemented.

## Proposed Support Plan

1. Validate both CLIs in WSL, Git Bash, and native PowerShell.
2. Decide whether the first Windows target is WSL-only or native PowerShell.
3. If native PowerShell is in scope, add `install.ps1` instead of asking users to
   run `install.sh` from PowerShell.
4. Add a Windows CI or manual smoke checklist:
   - install read-only
   - confirm `claude_chat` in config with secrets redacted
   - call `mcp__claude_chat__ask_claude`
   - initialize collaboration files
   - run a read-only watcher turn
   - verify `collaboration.lock` behavior
5. Update the platform badge only after a real Windows smoke test passes.
