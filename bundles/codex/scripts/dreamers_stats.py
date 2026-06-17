#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

from dreamers_node_launcher import find_node_runtime_root, run_node_entrypoint


def _runtime_candidate(path: Path) -> Path | None:
    candidate = path.expanduser()
    runtime_path = candidate / "dreamers_stats" / "cli.py"
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


def _needs_client_args(arguments: list[str]) -> bool:
    return "--client" not in arguments and all(not arg.startswith("--client=") for arg in arguments)


def _needs_home_args(arguments: list[str]) -> bool:
    prefixes = ("--home", "--codex-home")
    return all(arg not in prefixes and not arg.startswith("--home=") and not arg.startswith("--codex-home=") for arg in arguments)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        node_root = find_node_runtime_root("cli.js", __file__)
        if node_root is not None:
            node_result = run_node_entrypoint(node_root, "cli.js", arguments)
            if node_result is not None:
                return node_result
        runtime_root = _find_runtime_root()
        sys.path.insert(0, str(runtime_root))
        from dreamers_stats.cli import main as runtime_main

        return runtime_main(arguments)

    command, *tail = arguments
    runtime_args = [command]
    if _needs_client_args(arguments):
        runtime_args.extend(["--client", "codex"])
    if _needs_home_args(arguments):
        runtime_args.extend(["--home", os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))])
    runtime_args.extend(tail)

    node_root = find_node_runtime_root("cli.js", __file__)
    if node_root is not None:
        node_result = run_node_entrypoint(node_root, "cli.js", runtime_args)
        if node_result is not None:
            return node_result

    runtime_root = _find_runtime_root()
    sys.path.insert(0, str(runtime_root))
    from dreamers_stats.cli import main as runtime_main
    return runtime_main(runtime_args)


if __name__ == "__main__":
    raise SystemExit(main())
