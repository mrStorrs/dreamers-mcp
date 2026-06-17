# dreamers-mcp

Shared Dreamers stats runtime for Copilot and Codex.

## What it provides

- A TypeScript/Node runtime for Dreamers stats storage, validation, redaction, reports, dashboard rendering, CLI commands, and MCP tools
- A CLI surface that preserves current stats commands while adding explicit client routing
- A stdio MCP server that exposes report tools and explicit stat write operations
- Optional client bundles so `dreamers-copilot` and `dreamers-codex` can stay stats-free by default
- Python compatibility shims for historical installers and fallback launch behavior

## Requirements

- Node.js 20 or newer
- npm with the checked-in `package-lock.json`
- Python 3 for the installer orchestration scripts and retained compatibility tests. `npm run test:compat` resolves `py -3` on Windows and `python3` or `python` on POSIX systems.

From a clean checkout:

```bash
npm ci
npm run validate
```

`npm run validate` runs the TypeScript type-check, TypeScript regression suite, production build, and retained Python compatibility tests. The production build writes the package entrypoints to `dist/cli.js` and `dist/mcp-server.js`.

## Optional Copilot bundle

Base `dreamers-copilot` installs do not include stats hooks or runtime assets.

To opt into Copilot stats, install the optional bundle from this repo into a Copilot home:

```powershell
.\Install-DreamersMcpCopilot.ps1
```

Options:
- `-CopilotHome "D:\custom\.copilot"` to target a non-default Copilot home
- `-DreamersMcpPath "D:\projects\dreamers-mcp"` to copy the runtime from another local checkout
- `-Force` to overwrite existing bundle-managed files

The installer copies the TypeScript runtime into `dreamers/runtime/dreamers_mcp_node/`, copies the Python compatibility runtime into `dreamers/runtime/dreamers_stats/`, installs the Copilot compatibility shim and hook wrappers into `dreamers/scripts/`, installs the hook config into `hooks/`, and records exactly which files it copied in `dreamers/install-state/runtime-hooks.txt`. Hook wrappers prefer the Node runtime and use the Python runtime as a fallback.

Remove the optional bundle without touching historical stats data:

```powershell
.\Remove-DreamersMcpCopilot.ps1
```

## Optional Codex bundle

Base `dreamers-codex` installs do not include stats hooks, Dreamers MCP registration, or local `dreamers-mcp` runtime files.

To opt into Codex stats and the shared Dreamers stats MCP server, install the optional bundle from this repo into a Codex home:

Linux:

```bash
./Install-DreamersMcpCodex.sh
```

Windows:

```powershell
.\Install-DreamersMcpCodex.ps1
```

Options:
- `--codex-home /tmp/codex-home` or `-CodexHome "D:\custom\.codex"` to target a non-default Codex home
- `--dreamers-mcp-path /path/to/dreamers-mcp` or `-DreamersMcpPath "D:\projects\dreamers-mcp"` to copy the runtime from another local checkout
- `--force` or `-Force` to overwrite existing bundle-managed files

The installer copies the TypeScript runtime into `dreamers/runtime/dreamers_mcp_node/`, copies the Python compatibility runtime into `dreamers/runtime/dreamers_stats/`, installs the Codex compatibility shim and MCP server launcher into `dreamers/scripts/`, installs a Dreamers stats ref into `dreamers/refs/`, merges managed Dreamers hook handlers into `hooks.json`, appends a managed Dreamers MCP block to `config.toml`, and prepends a managed Dreamers stats block to `AGENTS.md`. That global `AGENTS.md` block only activates when Codex is running a `dreamers-*` skill, and it points at the separate `dreamers/refs/dreamers-mcp-stats.md` ref so Dreamers bookends stay owned by `dreamers-mcp` instead of `dreamers-codex`. Hook wrappers and the MCP launcher prefer the Node runtime and use the Python runtime as a fallback. If an existing `hooks.json`, `config.toml`, or `AGENTS.md` is not safe to merge automatically, the installer leaves that file unchanged and prints exact manual registration snippets.

Remove the optional bundle without touching historical stats data:

Linux:

```bash
./Remove-DreamersMcpCodex.sh
```

Windows:

```powershell
.\Remove-DreamersMcpCodex.ps1
```

## Validation

```bash
npm run typecheck
npm test
npm run build
npm run test:compat
```

Use `npm run validate` to run those commands as one local gate. `npm test` is the primary TypeScript regression authority. `npm run test:compat` keeps installer, historical manifest, data-preservation, and Python fallback behavior covered while existing users migrate. That compatibility command runs through `node tests-ts/run-compat-tests.mjs` so Python resolution is the same on Windows and POSIX systems.

## CLI examples

Run these from a checkout after `npm run build`, or use the installed `dreamers-stats` package binary when this package is installed as a dependency:

POSIX shells:

```bash
node dist/cli.js doctor --client copilot --home /tmp/copilot-home --json
node dist/cli.js summarize --client codex --home /tmp/codex-home --json
node dist/cli.js dashboard --client codex --home /tmp/codex-home --repo all --output /tmp/dreamers-stats.html
node dist/cli.js hook --client copilot --home /tmp/copilot-home --event-name sessionStart < payload.json
node dist/cli.js hook --client codex --home /tmp/codex-home --event-name UserPromptSubmit < payload.json
```

PowerShell:

```powershell
node dist/cli.js doctor --client copilot --home $env:TEMP\copilot-home --json
node dist/cli.js summarize --client codex --home $env:TEMP\codex-home --json
node dist/cli.js dashboard --client codex --home $env:TEMP\codex-home --repo all --output $env:TEMP\dreamers-stats.html
Get-Content .\payload.json -Raw | node dist/cli.js hook --client copilot --home $env:TEMP\copilot-home --event-name sessionStart
Get-Content .\payload.json -Raw | node dist/cli.js hook --client codex --home $env:TEMP\codex-home --event-name UserPromptSubmit
```

The `dashboard` command renders a standalone HTML file from the existing bounded reports. It formats large counts and token totals with commas, shows generated and data-range timestamps in readable UTC text, uses status badges for run groups, and lays out gate and token details in tables. Primary cards and clickable run details use reliable confirmed-closed runs, while incomplete or ambiguous runs are listed separately for diagnosis. Review totals include current-repo `.dreamers/reviews/*.md` artifacts when no stats event references them, without double-counting artifacts already backed by `review_pass_completed` events. Omit `--output` to print the HTML document to stdout. Use `--repo all` for a first smoke test, or run `--repo current` from the repository whose stats you want to inspect. Warning text is spaced for readability, and the dashboard still shows `n/a` or unavailable when exact token totals are missing.

For Codex, `Stop` hooks record exact token totals when a matching local session JSONL contains token-count data. For Copilot, `sessionEnd` hooks record exact session totals when `.copilot/session-state/<session_id>/events.jsonl` contains `session.shutdown` model metrics. Reports also resolve older unavailable token rows from local session logs when possible. Stats keep the unavailable fallback when exact data is missing, and the dashboard token card shows `n/a` or unavailable rather than `0` in that case.

Compatibility aliases remain available for current Copilot callers:

```bash
node dist/cli.js doctor --copilot-home /tmp/copilot-home --json
```

## MCP smoke check

Run this from a checkout after `npm run build`:

POSIX shells:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' | node dist/mcp-server.js
```

PowerShell:

```powershell
@'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
'@ | node dist/mcp-server.js
```

The installed Codex bundle registers the same Node MCP server through `dreamers/scripts/dreamers_mcp_server.py`, which delegates to the bundled Node runtime when available.
