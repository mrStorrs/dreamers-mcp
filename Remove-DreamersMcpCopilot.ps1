<#
.SYNOPSIS
    Removes the optional Dreamers MCP Copilot stats bundle.

.DESCRIPTION
    Removes only assets recorded in the Copilot runtime install manifest and
    preserves historical stats plus user-owned files that were never copied by
    the installer.
#>
[CmdletBinding()]
param(
    [string]$CopilotHome = (Join-Path $HOME ".copilot"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
$RuntimeInstallStatePath = Join-Path $CopilotHome "dreamers" "install-state" "runtime-hooks.txt"

function Get-FileHashString {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Remove-EmptyDirectory {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    $remaining = Get-ChildItem $Path -Force
    if ($remaining.Count -eq 0) {
        Remove-Item $Path -Force
        Write-Host "  REMOVED empty dir: $Path" -ForegroundColor DarkGray
    }
}

function Resolve-ManifestSourcePath {
    param([string]$RelativePath)

    if ($RelativePath.StartsWith("dreamers/runtime/dreamers_stats/")) {
        return Join-Path $RepoRoot ($RelativePath.Replace("dreamers/runtime/dreamers_stats/", "dreamers_stats/"))
    }
    if ($RelativePath.StartsWith("dreamers/scripts/")) {
        return Join-Path $RepoRoot ($RelativePath.Replace("dreamers/scripts/", "bundles/copilot/scripts/"))
    }
    if ($RelativePath.StartsWith("hooks/")) {
        return Join-Path $RepoRoot ($RelativePath.Replace("hooks/", "bundles/copilot/hooks/"))
    }
    return $null
}

function Get-ManagedTargets {
    $targets = @{}
    if (-not (Test-Path $RuntimeInstallStatePath)) {
        return $targets
    }
    foreach ($line in Get-Content $RuntimeInstallStatePath) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }
        $parts = $trimmed.Split("|", 2)
        $pathKey = $parts[0]
        $hashValue = if ($parts.Count -gt 1) { $parts[1] } else { "" }
        $targets[$pathKey] = $hashValue
    }
    return $targets
}

$managedTargets = Get-ManagedTargets
$total = 0
$verb = if ($DryRun) { "Dreamers MCP Copilot Bundle Remover (DRY RUN)" } else { "Dreamers MCP Copilot Bundle Remover" }

Write-Host "`n$verb" -ForegroundColor Cyan
Write-Host "Target: $CopilotHome`n"

Write-Host "[runtime-manifest]" -ForegroundColor Cyan
foreach ($relativePath in ($managedTargets.Keys | Sort-Object)) {
    $target = Join-Path $CopilotHome ($relativePath -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path $target)) {
        continue
    }
    $expectedHash = $managedTargets[$relativePath]
    if (-not $expectedHash) {
        $sourcePath = Resolve-ManifestSourcePath -RelativePath $relativePath
        if ($sourcePath) {
            $expectedHash = Get-FileHashString -Path $sourcePath
        }
    }
    $currentHash = Get-FileHashString -Path $target
    if ($expectedHash -and $currentHash -ne $expectedHash) {
        Write-Host "  SKIP (modified or user-owned): $relativePath" -ForegroundColor Yellow
        continue
    }
    if ($DryRun) {
        Write-Host "  WOULD REMOVE: $target" -ForegroundColor Yellow
    } else {
        Remove-Item $target -Force
        Write-Host "  REMOVED: $relativePath" -ForegroundColor Red
    }
    $total++
}

if (-not $DryRun) {
    if (Test-Path $RuntimeInstallStatePath) {
        Remove-Item $RuntimeInstallStatePath -Force
    }
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "dreamers" "install-state")
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "dreamers" "runtime" "dreamers_stats")
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "dreamers" "runtime")
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "dreamers" "scripts")
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "dreamers")
    Remove-EmptyDirectory -Path (Join-Path $CopilotHome "hooks")
}

$action = if ($DryRun) { "Would remove" } else { "Removed" }
Write-Host "`n$action $total bundle file(s).`n" -ForegroundColor Cyan
