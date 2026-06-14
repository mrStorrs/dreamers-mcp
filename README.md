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

## Validation

```bash
python3 -m py_compile dreamers_stats/*.py
python3 -m unittest discover -s tests
```

## CLI examples

```bash
python3 -m dreamers_stats doctor --client copilot --home /tmp/copilot-home --json
python3 -m dreamers_stats summarize --client codex --home /tmp/codex-home --json
python3 -m dreamers_stats hook --client copilot --home /tmp/copilot-home --event-name sessionStart < payload.json
```

Compatibility aliases remain available for current Copilot callers:

```bash
python3 -m dreamers_stats doctor --copilot-home /tmp/copilot-home --json
```
