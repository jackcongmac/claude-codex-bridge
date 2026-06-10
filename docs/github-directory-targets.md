# GitHub Directory Targets

These are GitHub-only promotion targets for submitting
`claude-codex-bridge` through pull requests or issues.

## Priority 1

| Repository | Format | Why it fits | Suggested listing |
| --- | --- | --- | --- |
| [`punkpeye/awesome-mcp-servers`](https://github.com/punkpeye/awesome-mcp-servers) | PR to `README.md` | Large MCP server catalog. | `[claude-codex-bridge](https://github.com/jackcongmac/claude-codex-bridge) - Bidirectional MCP bridge letting Claude Code and Codex call each other as tools, with persistent project-aware Claude sessions.` |
| [`appcypher/awesome-mcp-servers`](https://github.com/appcypher/awesome-mcp-servers) | PR to `README.md` | MCP server list with development tooling categories. | `[Claude Codex Bridge](https://github.com/jackcongmac/claude-codex-bridge) - Local MCP bridge for Claude Code and Codex collaboration with shared project memory.` |
| [`RoggeOhta/awesome-codex-cli`](https://github.com/RoggeOhta/awesome-codex-cli) | Issue or PR to `README.md` | Codex CLI ecosystem list. | `[claude-codex-bridge](https://github.com/jackcongmac/claude-codex-bridge) - Bidirectional Claude Code <-> Codex MCP bridge with persistent Claude colleague sessions.` |
| [`milisp/awesome-codex-cli`](https://github.com/milisp/awesome-codex-cli) | PR to `README.md` | Codex CLI tools list with MCP and development tooling sections. | `[claude-codex-bridge](https://github.com/jackcongmac/claude-codex-bridge) - Bidirectional MCP bridge for Codex CLI and Claude Code collaboration, including shared collaboration files and persistent sessions.` |
| [`hesreallyhim/awesome-claude-code`](https://github.com/hesreallyhim/awesome-claude-code) | PR or issue | Major Claude Code ecosystem list. | `Claude Codex Bridge, Tooling / Orchestrators, https://github.com/jackcongmac/claude-codex-bridge - Bidirectional MCP bridge that lets Claude Code and Codex call each other as project-aware collaborators.` |

## Priority 2

| Repository | Format | Why it fits | Caveat |
| --- | --- | --- | --- |
| [`punkpeye/awesome-mcp-devtools`](https://github.com/punkpeye/awesome-mcp-devtools) | PR to `README.md` | MCP developer tooling list. | Slightly weaker fit because the bridge is runtime workflow tooling. |
| [`rohitg00/awesome-claude-code-toolkit`](https://github.com/rohitg00/awesome-claude-code-toolkit) | PR to `README.md` | Claude Code toolkit catalog. | List under ecosystem/MCP rather than plugin categories. |
| [`e2b-dev/awesome-ai-sdks`](https://github.com/e2b-dev/awesome-ai-sdks) | PR to `README.md` | Broader agent and AI SDK/tooling catalog. | Less targeted than MCP/Codex/Claude lists. |

## Hold Until Packaged

| Repository | Format | Why to wait |
| --- | --- | --- |
| [`hashgraph-online/awesome-codex-plugins`](https://github.com/hashgraph-online/awesome-codex-plugins) | Issue first, PR later | Likely expects `.codex-plugin/plugin.json` and marketplace metadata. |
| [`ccplugins/awesome-claude-code-plugins`](https://github.com/ccplugins/awesome-claude-code-plugins) | Issue first, PR later | Stronger fit after wrapping as a Claude Code plugin or documented plugin-style install. |

## PR Strategy

1. Start with the two MCP server catalogs.
2. Submit to the two Codex lists.
3. Submit to the Claude Code ecosystem list.
4. Wait on plugin-specific lists until the project has plugin metadata or the
   maintainers confirm a non-plugin entry is acceptable.

Use the concise pitch from `docs/github-promotion-plan.md` and link directly to
`examples/review-loop.md` when maintainers ask what makes the project different.
