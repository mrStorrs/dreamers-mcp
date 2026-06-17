#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

from dreamers_node_launcher import find_node_runtime_root, run_node_entrypoint


def _runtime_candidate(path: Path) -> Path | None:
    candidate = path.expanduser()
    runtime_path = candidate / "dreamers_stats" / "mcp_server.py"
    return candidate if runtime_path.is_file() else None


def _find_runtime_root() -> Path:
    configured = os.environ.get("DREAMERS_MCP_HOME")
    if configured:
        resolved = _runtime_candidate(Path(configured))
        if resolved is None:
            raise RuntimeError(f"shared dreamers-mcp runtime not found at '{Path(configured).expanduser()}'")
        return resolved

    script_path = Path(__file__).resolve()
    candidates = [script_path.parent.parent / "runtime"]
    if len(script_path.parents) > 3:
        candidates.append(script_path.parents[3])
    for candidate in candidates:
        resolved = _runtime_candidate(candidate)
        if resolved is not None:
            return resolved
    raise RuntimeError("shared dreamers-mcp runtime not found; reinstall dreamers-mcp or set DREAMERS_MCP_HOME")


def main() -> int:
    node_root = find_node_runtime_root("mcp-server.js", __file__)
    if node_root is not None:
        node_result = run_node_entrypoint(node_root, "mcp-server.js", [])
        if node_result is not None:
            return node_result

    runtime_root = _find_runtime_root()
    sys.path.insert(0, str(runtime_root))
    from dreamers_stats.mcp_server import serve

    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
