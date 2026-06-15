# dreamers-mcp

Shared Dreamers stats runtime for Copilot and Codex.

## What it provides

- A standard-library-only Python package for Dreamers stats storage, validation, redaction, and reports
- A CLI surface that preserves current stats commands while adding explicit client routing
- A stdio MCP server that exposes report tools and explicit stat write operations
- Optional client bundles so `dreamers-copilot` and `dreamers-codex` can stay stats-free by default

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

The installer copies the shared runtime into `dreamers/runtime/dreamers_stats/`, installs the Copilot compatibility shim and hook wrappers into `dreamers/scripts/`, installs the hook config into `hooks/`, and records exactly which files it copied in `dreamers/install-state/runtime-hooks.txt`.

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

The installer copies the shared runtime into `dreamers/runtime/dreamers_stats/`, installs the Codex compatibility shim and MCP server launcher into `dreamers/scripts/`, installs a Dreamers stats ref into `dreamers/refs/`, merges managed Dreamers hook handlers into `hooks.json`, appends a managed Dreamers MCP block to `config.toml`, and prepends a managed Dreamers stats block to `AGENTS.md`. That global `AGENTS.md` block only activates when Codex is running a `dreamers-*` skill, and it points at the separate `dreamers/refs/dreamers-mcp-stats.md` ref so Dreamers bookends stay owned by `dreamers-mcp` instead of `dreamers-codex`. If an existing `hooks.json`, `config.toml`, or `AGENTS.md` is not safe to merge automatically, the installer leaves that file unchanged and prints exact manual registration snippets.

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
python3 -m py_compile dreamers_stats/*.py bundles/copilot/scripts/dreamers_stats.py bundles/codex/scripts/*.py tests/bundle_test_support.py tests/test_copilot_bundle.py tests/test_codex_bundle.py tests/test_shared_stats.py
python3 -m unittest discover -s tests
```

## CLI examples

```bash
python3 -m dreamers_stats doctor --client copilot --home /tmp/copilot-home --json
python3 -m dreamers_stats summarize --client codex --home /tmp/codex-home --json
python3 -m dreamers_stats dashboard --client codex --home /tmp/codex-home --repo all --output /tmp/dreamers-stats.html
python3 -m dreamers_stats hook --client copilot --home /tmp/copilot-home --event-name sessionStart < payload.json
python3 -m dreamers_stats hook --client codex --home /tmp/codex-home --event-name UserPromptSubmit < payload.json
```

The `dashboard` command renders a standalone HTML file from the existing bounded reports. Omit `--output` to print the HTML document to stdout. Use `--repo all` for a first smoke test, or run `--repo current` from the repository whose stats you want to inspect.

For Codex, `Stop` hooks record exact token totals when a matching local session JSONL contains token-count data. For Copilot, `sessionEnd` hooks record exact session totals when `.copilot/session-state/<session_id>/events.jsonl` contains `session.shutdown` model metrics. Reports also resolve older unavailable token rows from local session logs when possible. Stats keep the unavailable fallback when exact data is missing, and the dashboard token card shows `n/a` or unavailable rather than `0` in that case.

Compatibility aliases remain available for current Copilot callers:

```bash
python3 -m dreamers_stats doctor --copilot-home /tmp/copilot-home --json
```
