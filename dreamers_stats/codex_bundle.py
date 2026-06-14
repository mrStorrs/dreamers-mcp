from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


RUNTIME_FILES = ("__init__.py", "__main__.py", "cli.py", "codex_bundle.py", "mcp_server.py", "runtime.py")
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)
INSTALL_STATE_RELATIVE = Path("dreamers") / "install-state" / "codex-bundle.json"
RUNTIME_TARGET_RELATIVE = Path("dreamers") / "runtime" / "dreamers_stats"
SCRIPTS_TARGET_RELATIVE = Path("dreamers") / "scripts"
HOOKS_CONFIG_RELATIVE = Path("hooks.json")
MCP_CONFIG_RELATIVE = Path("config.toml")
MANAGED_BLOCK_BEGIN = "# BEGIN DREAMERS MCP CODEX BUNDLE"
MANAGED_BLOCK_END = "# END DREAMERS MCP CODEX BUNDLE"
MANAGED_SERVER_ID = "dreamers_stats"
REQUIRED_MANAGED_BUNDLE_KEYS = {
    (RUNTIME_TARGET_RELATIVE / name).as_posix()
    for name in RUNTIME_FILES
}
REQUIRED_MANAGED_BUNDLE_KEYS.update(
    (SCRIPTS_TARGET_RELATIVE / name).as_posix()
    for name in ("dreamers_hook.ps1", "dreamers_hook.sh", "dreamers_mcp_server.py", "dreamers_stats.py")
)


@dataclass
class InstallResult:
    installed_count: int
    hook_configured: bool
    mcp_configured: bool
    warnings: list[str]
    manual_steps: list[str]


@dataclass
class RemoveResult:
    removed_count: int
    warnings: list[str]


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(codex_home: Path) -> dict[str, object]:
    manifest_path = codex_home / INSTALL_STATE_RELATIVE
    if not manifest_path.exists():
        return {"files": {}}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": {}}
    files = payload.get("files")
    if not isinstance(files, dict):
        return {"files": {}}
    return {"files": {str(key): str(value) for key, value in files.items()}}


def write_manifest(codex_home: Path, manifest: dict[str, object]) -> None:
    manifest_path = codex_home / INSTALL_STATE_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_checkout_root(candidate: str | Path | None, fallback_root: Path) -> Path:
    checkout_root = Path(candidate).expanduser() if candidate else fallback_root
    package_dir = checkout_root / "dreamers_stats"
    bundle_dir = checkout_root / "bundles" / "codex" / "scripts"
    if not package_dir.is_dir():
        raise RuntimeError(
            f"Cannot find dreamers-mcp shared runtime at '{checkout_root}'. "
            "Pass --dreamers-mcp-path to a local dreamers-mcp checkout."
        )
    for name in RUNTIME_FILES:
        if not (package_dir / name).is_file():
            raise RuntimeError(f"dreamers-mcp checkout at '{checkout_root}' is incomplete; missing dreamers_stats/{name}.")
    if not bundle_dir.is_dir():
        raise RuntimeError(f"dreamers-mcp checkout at '{checkout_root}' is missing bundles/codex/scripts.")
    return checkout_root


def iter_runtime_sources(checkout_root: Path) -> list[tuple[Path, Path]]:
    package_dir = checkout_root / "dreamers_stats"
    return [
        (package_dir / name, RUNTIME_TARGET_RELATIVE / name)
        for name in sorted(RUNTIME_FILES)
    ]


def iter_bundle_sources(checkout_root: Path) -> list[tuple[Path, Path]]:
    bundle_dir = checkout_root / "bundles" / "codex" / "scripts"
    return [
        (path, SCRIPTS_TARGET_RELATIVE / path.name)
        for path in sorted(bundle_dir.iterdir())
        if path.is_file()
    ]


def copy_bundle_files(
    checkout_root: Path,
    codex_home: Path,
    *,
    force: bool,
) -> tuple[int, dict[str, str]]:
    previous_manifest = load_manifest(codex_home).get("files", {})
    previous_files = previous_manifest if isinstance(previous_manifest, dict) else {}
    managed_files: dict[str, str] = {}
    copied = 0
    for source_path, relative_path in [*iter_runtime_sources(checkout_root), *iter_bundle_sources(checkout_root)]:
        target_path = codex_home / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_hash = sha256_path(source_path)
        relative_key = relative_path.as_posix()
        if target_path.exists() and not force:
            target_hash = sha256_path(target_path)
            if target_hash == source_hash:
                managed_files[relative_key] = source_hash
                continue
            if previous_files.get(relative_key) == target_hash:
                managed_files[relative_key] = target_hash
                continue
            continue
        shutil.copy2(source_path, target_path)
        managed_files[relative_key] = source_hash
        copied += 1
    write_manifest(codex_home, {"files": managed_files})
    return copied, managed_files


def posix_hook_command(codex_home: Path, event_name: str) -> str:
    script_path = codex_home / SCRIPTS_TARGET_RELATIVE / "dreamers_hook.sh"
    return f"bash {shlex.quote(str(script_path))} {shlex.quote(event_name)}"


def windows_hook_command(codex_home: Path, event_name: str) -> str:
    script_path = codex_home / SCRIPTS_TARGET_RELATIVE / "dreamers_hook.ps1"
    escaped = str(script_path).replace('"', '""')
    return f'powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{escaped}" {event_name}'


def managed_hook_group(codex_home: Path, event_name: str) -> dict[str, object]:
    return {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": posix_hook_command(codex_home, event_name),
                "commandWindows": windows_hook_command(codex_home, event_name),
                "statusMessage": f"Dreamers stats: {event_name}",
            }
        ],
    }


def expected_hook_commands(codex_home: Path, event_name: str) -> tuple[str, str]:
    return posix_hook_command(codex_home, event_name), windows_hook_command(codex_home, event_name)


def is_managed_hook_group(group: object, codex_home: Path, event_name: str) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != 1:
        return False
    handler = hooks[0]
    if not isinstance(handler, dict):
        return False
    expected_command, expected_windows = expected_hook_commands(codex_home, event_name)
    return (
        handler.get("type") == "command"
        and handler.get("command") == expected_command
        and handler.get("commandWindows") == expected_windows
    )


def normalize_hooks_payload(raw_text: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, "hooks.json is not valid JSON"
    if not isinstance(payload, dict):
        return None, "hooks.json must be a JSON object"
    hooks = payload.get("hooks")
    if hooks is None:
        payload["hooks"] = {}
        return payload, None
    if not isinstance(hooks, dict):
        return None, "hooks.json top-level 'hooks' value must be an object"
    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            return None, f"hooks.json event '{event_name}' must be a list"
    return payload, None


def manual_hook_instructions(codex_home: Path) -> str:
    payload = {
        "hooks": {
            event_name: [managed_hook_group(codex_home, event_name)]
            for event_name in HOOK_EVENTS
        }
    }
    return "Manual hook registration:\n" + json.dumps(payload, indent=2, sort_keys=True)


def install_hooks_config(codex_home: Path) -> tuple[bool, list[str]]:
    config_path = codex_home / HOOKS_CONFIG_RELATIVE
    warnings: list[str] = []
    if config_path.exists():
        payload, error = normalize_hooks_payload(config_path.read_text(encoding="utf-8"))
        if error is not None or payload is None:
            warnings.append(error or "hooks.json is not safe to merge automatically")
            warnings.append(manual_hook_instructions(codex_home))
            return False, warnings
    else:
        payload = {"hooks": {}}

    hooks = payload["hooks"]
    assert isinstance(hooks, dict)
    for event_name in HOOK_EVENTS:
        groups = hooks.get(event_name, [])
        if not isinstance(groups, list):
            warnings.append(f"hooks.json event '{event_name}' is not safe to merge automatically")
            warnings.append(manual_hook_instructions(codex_home))
            return False, warnings
        retained = [group for group in groups if not is_managed_hook_group(group, codex_home, event_name)]
        retained.append(managed_hook_group(codex_home, event_name))
        hooks[event_name] = retained

    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True, warnings


def remove_hooks_config(codex_home: Path) -> list[str]:
    config_path = codex_home / HOOKS_CONFIG_RELATIVE
    if not config_path.exists():
        return []
    payload, error = normalize_hooks_payload(config_path.read_text(encoding="utf-8"))
    if error is not None or payload is None:
        return [f"Skipped hooks.json cleanup: {error or 'unsafe hook config'}"]

    hooks = payload["hooks"]
    assert isinstance(hooks, dict)
    for event_name in HOOK_EVENTS:
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        retained = [group for group in groups if not is_managed_hook_group(group, codex_home, event_name)]
        if retained:
            hooks[event_name] = retained
        else:
            hooks.pop(event_name, None)

    if hooks:
        config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        payload.pop("hooks", None)
        if payload:
            config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            config_path.unlink()
    return []


def mcp_block(codex_home: Path, launcher_command: str, launcher_args: list[str]) -> str:
    script_path = codex_home / SCRIPTS_TARGET_RELATIVE / "dreamers_mcp_server.py"
    args = [*launcher_args, str(script_path)]
    args_json = json.dumps(args)
    return (
        f"{MANAGED_BLOCK_BEGIN}\n"
        f"[mcp_servers.{MANAGED_SERVER_ID}]\n"
        f'command = "{launcher_command}"\n'
        f"args = {args_json}\n"
        f"{MANAGED_BLOCK_END}\n"
    )


def manual_mcp_instructions(codex_home: Path, launcher_command: str, launcher_args: list[str]) -> str:
    return "Manual MCP registration:\n" + mcp_block(codex_home, launcher_command, launcher_args).strip()


def find_managed_block(text: str) -> tuple[int, int] | None:
    start = text.find(MANAGED_BLOCK_BEGIN)
    end = text.find(MANAGED_BLOCK_END)
    if start == -1 and end == -1:
        return None
    if start == -1 or end == -1 or end < start:
        raise ValueError("managed block markers are malformed")
    end_index = end + len(MANAGED_BLOCK_END)
    if end_index < len(text) and text[end_index:end_index + 1] == "\n":
        end_index += 1
    return start, end_index


def install_mcp_config(codex_home: Path, launcher_command: str, launcher_args: list[str]) -> tuple[bool, list[str]]:
    config_path = codex_home / MCP_CONFIG_RELATIVE
    warnings: list[str] = []
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
    else:
        text = ""
    try:
        block_range = find_managed_block(text)
    except ValueError as exc:
        warnings.append(f"Skipped config.toml merge: {exc}")
        warnings.append(manual_mcp_instructions(codex_home, launcher_command, launcher_args))
        return False, warnings

    if block_range is None and f"[mcp_servers.{MANAGED_SERVER_ID}]" in text:
        warnings.append("Skipped config.toml merge: existing unmanaged dreamers_stats MCP entry")
        warnings.append(manual_mcp_instructions(codex_home, launcher_command, launcher_args))
        return False, warnings

    block = mcp_block(codex_home, launcher_command, launcher_args)
    if block_range is not None:
        start, end = block_range
        updated = text[:start] + block + text[end:]
    else:
        updated = text
        if updated and not updated.endswith("\n"):
            updated += "\n"
        if updated:
            updated += "\n"
        updated += block
    config_path.write_text(updated, encoding="utf-8")
    return True, warnings


def remove_mcp_config(codex_home: Path) -> list[str]:
    config_path = codex_home / MCP_CONFIG_RELATIVE
    if not config_path.exists():
        return []
    text = config_path.read_text(encoding="utf-8")
    try:
        block_range = find_managed_block(text)
    except ValueError as exc:
        return [f"Skipped config.toml cleanup: {exc}"]
    if block_range is None:
        return []
    start, end = block_range
    updated = text[:start] + text[end:]
    updated = updated.strip()
    if updated:
        config_path.write_text(updated + "\n", encoding="utf-8")
    else:
        config_path.unlink()
    return []


def remove_empty_parents(codex_home: Path, relative_path: Path) -> None:
    current = (codex_home / relative_path).parent
    stop_at = codex_home
    while current != stop_at.parent and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        if current == stop_at:
            break
        current = current.parent


def install_bundle(
    codex_home: Path,
    checkout_root: Path,
    *,
    force: bool,
    launcher_command: str,
    launcher_args: list[str],
) -> InstallResult:
    installed_count, managed_files = copy_bundle_files(checkout_root, codex_home, force=force)
    missing_keys = sorted(REQUIRED_MANAGED_BUNDLE_KEYS - set(managed_files))
    if missing_keys:
        hook_configured = False
        mcp_configured = False
        hook_warnings = [
            "Skipped hook and MCP configuration because required bundle assets were not installed cleanly: "
            + ", ".join(missing_keys)
        ]
        mcp_warnings = []
    else:
        hook_configured, hook_warnings = install_hooks_config(codex_home)
        mcp_configured, mcp_warnings = install_mcp_config(codex_home, launcher_command, launcher_args)
    write_manifest(codex_home, {"files": managed_files})
    warnings = [*hook_warnings, *mcp_warnings]
    manual_steps = [item for item in warnings if item.startswith("Manual ")]
    return InstallResult(
        installed_count=installed_count,
        hook_configured=hook_configured,
        mcp_configured=mcp_configured,
        warnings=[item for item in warnings if not item.startswith("Manual ")],
        manual_steps=manual_steps,
    )


def remove_bundle(codex_home: Path) -> RemoveResult:
    manifest = load_manifest(codex_home)
    files = manifest.get("files", {})
    managed_files = files if isinstance(files, dict) else {}
    removed_count = 0
    for relative_key in sorted(managed_files):
        target_path = codex_home / Path(relative_key)
        if not target_path.exists():
            continue
        expected_hash = str(managed_files[relative_key])
        current_hash = sha256_path(target_path)
        if expected_hash and current_hash != expected_hash:
            continue
        target_path.unlink()
        removed_count += 1
        remove_empty_parents(codex_home, Path(relative_key))

    warnings = [*remove_hooks_config(codex_home), *remove_mcp_config(codex_home)]

    manifest_path = codex_home / INSTALL_STATE_RELATIVE
    if manifest_path.exists():
        manifest_path.unlink()
        remove_empty_parents(codex_home, INSTALL_STATE_RELATIVE)
    return RemoveResult(removed_count=removed_count, warnings=warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dreamers_stats.codex_bundle")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install_parser = subcommands.add_parser("install")
    install_parser.add_argument("--codex-home", required=True)
    install_parser.add_argument("--dreamers-mcp-path")
    install_parser.add_argument("--force", action="store_true")
    install_parser.add_argument("--launcher-command", required=True)
    install_parser.add_argument("--launcher-arg", action="append", default=[])

    remove_parser = subcommands.add_parser("remove")
    remove_parser.add_argument("--codex-home", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    module_root = Path(__file__).resolve().parents[1]

    if args.command == "install":
        checkout_root = resolve_checkout_root(args.dreamers_mcp_path, module_root)
        result = install_bundle(
            Path(args.codex_home).expanduser(),
            checkout_root,
            force=args.force,
            launcher_command=args.launcher_command,
            launcher_args=list(args.launcher_arg),
        )
        print("\nDreamers MCP Codex Bundle Installer")
        print(f"Bundle:  {checkout_root / 'bundles' / 'codex'}")
        print(f"Runtime: {checkout_root / 'dreamers_stats'}")
        print(f"Target:  {Path(args.codex_home).expanduser()}\n")
        print(f"Installed {result.installed_count} bundle file(s).")
        if result.hook_configured:
            print("Configured hooks.json with managed Dreamers entries.")
        if result.mcp_configured:
            print("Configured config.toml with managed Dreamers MCP entry.")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for manual in result.manual_steps:
            print(manual)
        return 0

    result = remove_bundle(Path(args.codex_home).expanduser())
    print("\nDreamers MCP Codex Bundle Remover")
    print(f"Target: {Path(args.codex_home).expanduser()}\n")
    print(f"Removed {result.removed_count} bundle file(s).")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
