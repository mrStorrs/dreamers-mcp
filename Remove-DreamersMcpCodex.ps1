[CmdletBinding()]
param(
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" })
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
    throw "python runtime is required to remove the Dreamers MCP Codex bundle"
}

$pythonCommand = Resolve-PythonCommand
$pythonExecutable = $pythonCommand[0]
$pythonArgs = @()
if ($pythonCommand.Length -gt 1) {
    $pythonArgs = $pythonCommand[1..($pythonCommand.Length - 1)]
}

Push-Location $RepoRoot
try {
    & $pythonExecutable @pythonArgs -m dreamers_stats.codex_bundle remove --codex-home $CodexHome
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
