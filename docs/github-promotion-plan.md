# GitHub Promotion Plan

This plan keeps promotion inside GitHub: repository presentation, community
files, releases, issues, discussions, and pull requests to relevant GitHub
directories.

## Positioning

One-line pitch:

> A bidirectional MCP bridge that lets Claude Code and Codex call each other as
> tools, keep project context, and collaborate through a shared board.

Proof-of-work angle:

> This project was built with the same Claude + Codex collaboration workflow it
> enables.

## Repository conversion checklist

- [x] Put the Claude Code ↔ Codex value proposition in the README first screen.
- [x] Fix the install URL to the real repository URL.
- [x] Add a minimal review-loop example.
- [x] Add GitHub issue templates.
- [x] Add a PR template.
- [x] Add a dedicated security page.
- [ ] Add a terminal GIF or asciinema recording to the README.
- [ ] Create the first GitHub release from `docs/release-notes-v0.1.0.md`.
- [ ] Pin the repository on the owner's GitHub profile.

## GitHub launch sequence

1. Merge the promotion asset updates.
2. Create the `v0.1.0` release using `docs/release-notes-v0.1.0.md`.
3. Open a pinned issue: `How this project was built by Claude Code + Codex`.
4. Open roadmap issues with `help wanted` and `good first issue` labels.
5. Submit pull requests to GitHub awesome lists and MCP directories listed in
   [`docs/github-directory-targets.md`](github-directory-targets.md).
6. Use GitHub Discussions for installation Q&A and workflow examples.

## Suggested pinned issue

Title:

```text
How this project was built by Claude Code + Codex
```

Body:

```markdown
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
```

## Suggested roadmap issues

```text
Add a short terminal demo GIF to README
```

```text
Test install flow on Linux distributions
```

```text
Create a cookbook for review, test, and documentation workflows
```

```text
Document safe read-only setup with screenshots
```

```text
Explore Windows support constraints
```

## Suggested labels

- `documentation`
- `good first issue`
- `help wanted`
- `security`
- `workflow`
- `mcp`
- `examples`

## Awesome-list submission copy

```markdown
- [claude-codex-bridge](https://github.com/jackcongmac/claude-codex-bridge) -
  Bidirectional MCP bridge that lets Claude Code and Codex call each other as
  tools, with a persistent project-aware Claude colleague and shared
  collaboration files.
```
