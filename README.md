# dreamers-mcp

Shared Dreamers stats runtime for Copilot and Codex.

## What it provides

- A standard-library-only Python package for Dreamers stats storage, validation, redaction, and reports
- A CLI surface that preserves current stats commands while adding explicit client routing
- A stdio MCP server that exposes report tools and explicit stat write operations

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
