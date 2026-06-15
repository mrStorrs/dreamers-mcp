#!/usr/bin/env bash
set -euo pipefail

codex_home="${CODEX_HOME:-$HOME/.codex}"
dreamers_mcp_path=""
force=0

usage() {
  cat <<'EOF'
Usage: ./Install-DreamersMcpCodex.sh [--force] [--codex-home PATH] [--dreamers-mcp-path PATH]

Installs the optional Dreamers MCP Codex stats bundle into CODEX_HOME, or ~/.codex when CODEX_HOME is not set.
EOF
}

while (($#)); do
  case "$1" in
    -f|--force)
      force=1
      ;;
    --codex-home)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --codex-home" >&2; exit 1; }
      codex_home="$1"
      ;;
    --codex-home=*)
      codex_home="${1#*=}"
      ;;
    --dreamers-mcp-path)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --dreamers-mcp-path" >&2; exit 1; }
      dreamers_mcp_path="$1"
      ;;
    --dreamers-mcp-path=*)
      dreamers_mcp_path="${1#*=}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

resolve_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

if ! python_bin="$(resolve_python)"; then
  echo "python runtime is required to install the Dreamers MCP Codex bundle" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

args=(
  -m
  dreamers_stats.codex_bundle
  install
  --codex-home
  "$codex_home"
  --launcher-command
  "$python_bin"
)

if [[ -n "$dreamers_mcp_path" ]]; then
  args+=(--dreamers-mcp-path "$dreamers_mcp_path")
fi
if [[ $force -eq 1 ]]; then
  args+=(--force)
fi

"$python_bin" "${args[@]}"
