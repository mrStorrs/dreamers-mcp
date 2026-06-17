from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def node_runtime_candidate(path: Path, entrypoint: str) -> Path | None:
    candidate = path.expanduser()
    for root in (candidate, candidate / "runtime", candidate / "dreamers" / "runtime"):
        for runtime_root in (root, root / "dreamers_mcp_node"):
            runtime_path = runtime_root / "dist" / entrypoint
            if runtime_path.is_file():
                return runtime_root
    return None


def find_node_runtime_root(entrypoint: str, script_file: str | Path) -> Path | None:
    configured = os.environ.get("DREAMERS_MCP_HOME")
    if configured:
        return node_runtime_candidate(Path(configured), entrypoint)

    script_path = Path(script_file).resolve()
    candidates = [script_path.parent.parent / "runtime"]
    if len(script_path.parents) > 3:
        candidates.append(script_path.parents[3])
    for candidate in candidates:
        resolved = node_runtime_candidate(candidate, entrypoint)
        if resolved is not None:
            return resolved
    return None


def run_node_entrypoint(runtime_root: Path, entrypoint: str, arguments: list[str]) -> int | None:
    node_bin = os.environ.get("DREAMERS_STATS_NODE") or shutil.which("node")
    if node_bin is None:
        return None
    return subprocess.call([node_bin, str(runtime_root / "dist" / entrypoint), *arguments])
