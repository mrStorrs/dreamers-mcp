[CmdletBinding()]
param(
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }),
    [string]$DreamersMcpPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }

function Resolve-PythonCommand {
    foreach ($name in @("py", "python3", "python")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        if ($name -eq "py") {
            return @($command.Source, "-3")
        }
        return @($command.Source)
    }
    throw "python runtime is required to install the Dreamers MCP Codex bundle"
}

$pythonCommand = Resolve-PythonCommand
$pythonExecutable = $pythonCommand[0]
$pythonArgs = @()
if ($pythonCommand.Length -gt 1) {
    $pythonArgs = $pythonCommand[1..($pythonCommand.Length - 1)]
}

$moduleArgs = @(
    "-m",
    "dreamers_stats.codex_bundle",
    "install",
    "--codex-home",
    $CodexHome,
    "--launcher-command",
    $pythonExecutable
)

foreach ($item in $pythonArgs) {
    $moduleArgs += @("--launcher-arg", $item)
}

if ($DreamersMcpPath) {
    $moduleArgs += @("--dreamers-mcp-path", $DreamersMcpPath)
}
if ($Force) {
    $moduleArgs += "--force"
}

Push-Location $RepoRoot
try {
    & $pythonExecutable @pythonArgs @moduleArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
