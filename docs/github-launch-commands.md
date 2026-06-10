# GitHub Launch Commands

These commands are intentionally GitHub-only. Run them after authenticating the
GitHub CLI with an account that can write to `jackcongmac/claude-codex-bridge`.

## Authentication check

```bash
gh auth status
```

If the token is expired:

```bash
gh auth login -h github.com
```

## Create launch labels

```bash
gh label create documentation --color 0366d6 --description "Docs, examples, README, and guides"
gh label create workflow --color 5319e7 --description "Agent collaboration workflows"
gh label create mcp --color 7c4dff --description "Model Context Protocol integration"
gh label create examples --color 0e8a16 --description "Cookbook and example workflows"
gh label create security --color b60205 --description "Security model and safe defaults"
```

If a label already exists, skip it or update it in the GitHub UI.

## Open launch issues

```bash
gh issue create \
  --title "Add a short terminal demo GIF to README" \
  --label documentation,good\ first\ issue \
  --body "Create a short terminal recording that shows Codex calling Claude, Claude remembering the prior review, and Codex acting on the result. Add it near the top of README.md."
```

```bash
gh issue create \
  --title "Test install flow on Linux distributions" \
  --label help\ wanted \
  --body "Validate ./install.sh on common Linux environments. Report exact distro, shell, Claude CLI path, Codex CLI path, and whether Codex sees the claude_chat MCP server after restart."
```

```bash
gh issue create \
  --title "Create cookbook examples for review, test, and documentation workflows" \
  --label documentation,examples,workflow \
  --body "Add copy-pasteable examples for Claude review loops, Codex test execution loops, and documentation handoffs using collaboration.md."
```

```bash
gh issue create \
  --title "Document safe read-only setup with screenshots" \
  --label documentation,security \
  --body "Add a short guide showing BRIDGE_READONLY=1 install, CLAUDE_CHAT_ALLOWED_TOOLS=\"Read Grep Glob\", and how to confirm the effective configuration."
```

```bash
gh issue create \
  --title "Explore Windows support constraints" \
  --label help\ wanted \
  --body "Investigate what would be required for Windows support. Focus on Claude/Codex CLI availability, config paths, shell assumptions in install.sh, and watcher behavior."
```

## Create the proof-of-work issue

```bash
gh issue create \
  --title "How this project was built by Claude Code + Codex" \
  --label workflow \
  --body-file - <<'EOF'
This bridge is also its own proof of work.

The project was built through a Claude Code + Codex workflow:

- Codex asked Claude for review and second opinions.
- Claude kept project context across turns.
- Both sides coordinated through shared project files.
- The resulting bridge now makes that workflow reusable in other repositories.

If you try the bridge in your own project, share the workflow here:

- What did Claude handle well?
- What did Codex handle well?
- Where did the handoff break down?
- What should the bridge automate next?
EOF
```

Pin this issue in the GitHub UI after creating it.

## Create the first release

```bash
gh release create v0.1.0 \
  --title "v0.1.0" \
  --notes-file docs/release-notes-v0.1.0.md
```

## Submit to GitHub lists

Use this listing text when opening pull requests to relevant GitHub directories:

```markdown
- [claude-codex-bridge](https://github.com/jackcongmac/claude-codex-bridge) -
  Bidirectional MCP bridge that lets Claude Code and Codex call each other as
  tools, with a persistent project-aware Claude colleague and shared
  collaboration files.
```
