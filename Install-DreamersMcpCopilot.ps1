<#
.SYNOPSIS
    Installs the optional Dreamers MCP Copilot stats bundle.

.DESCRIPTION
    Copies the shared stats runtime, Copilot compatibility shim, and Copilot
    hook assets into the selected Copilot home without requiring package
    installation.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$CopilotHome = (Join-Path $HOME ".copilot"),
    [string]$DreamersMcpPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
$BundleSource = Join-Path $RepoRoot "bundles" "copilot"
$RuntimeInstallStatePath = Join-Path $CopilotHome "dreamers" "install-state" "runtime-hooks.txt"

function Get-FileHashString {
    param([string]$Path)
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-DreamersMcpCheckout {
    param([string]$CandidatePath)

    $checkoutRoot = if ($CandidatePath) { $CandidatePath } else { $RepoRoot }
    $resolvedPath = Resolve-Path -LiteralPath $checkoutRoot -ErrorAction SilentlyContinue
    $resolvedRoot = if ($null -ne $resolvedPath) {
        [System.IO.Path]::GetFullPath($resolvedPath.Path)
    } else {
        [System.IO.Path]::GetFullPath($checkoutRoot)
    }
    $packageDir = Join-Path $resolvedRoot "dreamers_stats"
    $distDir = Join-Path $resolvedRoot "dist"
    $requiredFiles = @("__init__.py", "__main__.py", "cli.py", "mcp_server.py", "runtime.py")
    if (-not (Test-Path $packageDir)) {
        throw "Cannot find dreamers-mcp shared runtime at '$resolvedRoot'. Pass -DreamersMcpPath to a local dreamers-mcp checkout."
    }
    foreach ($name in $requiredFiles) {
        if (-not (Test-Path (Join-Path $packageDir $name))) {
            throw "dreamers-mcp checkout at '$resolvedRoot' is incomplete; missing dreamers_stats/$name."
        }
    }
    if (-not (Test-Path (Join-Path $resolvedRoot "package.json"))) {
        throw "dreamers-mcp checkout at '$resolvedRoot' is incomplete; missing package.json."
    }
    foreach ($name in @("index.js", "cli.js", "mcp-server.js")) {
        if (-not (Test-Path (Join-Path $distDir $name))) {
            throw "dreamers-mcp checkout at '$resolvedRoot' is incomplete; missing dist/$name. Run npm run build first."
        }
    }
    if (-not (Test-Path ([System.IO.Path]::Combine($resolvedRoot, "bundles", "shared", "scripts", "dreamers_node_launcher.py")))) {
        throw "dreamers-mcp checkout at '$resolvedRoot' is incomplete; missing bundles/shared/scripts/dreamers_node_launcher.py."
    }
    return $resolvedRoot
}

function Get-ManagedRuntimeState {
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

function Copy-Files {
    param(
        [string]$From,
        [string]$To,
        [hashtable]$PreviousManagedState,
        [hashtable]$ManagedState,
        [string]$ManagedPrefix,
        [switch]$Recurse
    )
    if (-not (Test-Path $From)) {
        throw "Bundle source not found: $From"
    }
    if (-not (Test-Path $To)) {
        if ($PSCmdlet.ShouldProcess($To, "Create directory")) {
            New-Item -ItemType Directory -Path $To -Force | Out-Null
        }
    }
    $files = if ($Recurse) {
        Get-ChildItem $From -File -Recurse
    } else {
        Get-ChildItem $From -File
    }
    $count = 0
    foreach ($file in $files) {
        $relativePath = if ($Recurse) {
            $file.FullName.Substring($From.Length).TrimStart("\", "/")
        } else {
            $file.Name
        }
        $dest = Join-Path $To $relativePath
        $destDir = Split-Path -Parent $dest
        if (-not (Test-Path $destDir)) {
            if ($PSCmdlet.ShouldProcess($destDir, "Create directory")) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
        }
        $manifestPath = ((Join-Path $ManagedPrefix $relativePath) -replace "\\", "/")
        $sourceHash = Get-FileHashString -Path $file.FullName
        if ((Test-Path $dest) -and -not $Force) {
            $targetHash = Get-FileHashString -Path $dest
            if ($targetHash -eq $sourceHash) {
                $ManagedState[$manifestPath] = $sourceHash
                Write-Host "  OK (already current): $relativePath" -ForegroundColor DarkGray
                continue
            }
            if ($PreviousManagedState.ContainsKey($manifestPath) -and $PreviousManagedState[$manifestPath] -eq $targetHash) {
                if ($PSCmdlet.ShouldProcess($dest, "Refresh managed bundle asset")) {
                    Copy-Item $file.FullName $dest -Force
                    $ManagedState[$manifestPath] = $sourceHash
                    Write-Host "  OK (refreshed managed): $relativePath" -ForegroundColor Green
                    $count++
                }
                continue
            }
            Write-Host "  SKIP (exists): $relativePath - use -Force to overwrite" -ForegroundColor Yellow
            continue
        }
        if ($PSCmdlet.ShouldProcess($dest, "Copy bundle asset")) {
            Copy-Item $file.FullName $dest -Force
            $ManagedState[$manifestPath] = $sourceHash
            Write-Host "  OK: $relativePath" -ForegroundColor Green
            $count++
        }
    }
    return $count
}

function Copy-ManagedFile {
    param(
        [string]$From,
        [string]$To,
        [hashtable]$PreviousManagedState,
        [hashtable]$ManagedState,
        [string]$ManifestPath
    )
    if (-not (Test-Path $From)) {
        return 0
    }
    $destDir = Split-Path -Parent $To
    if (-not (Test-Path $destDir)) {
        if ($PSCmdlet.ShouldProcess($destDir, "Create directory")) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
    }
    $sourceHash = Get-FileHashString -Path $From
    if ((Test-Path $To) -and -not $Force) {
        $targetHash = Get-FileHashString -Path $To
        if ($targetHash -eq $sourceHash) {
            $ManagedState[$ManifestPath] = $sourceHash
            Write-Host "  OK (already current): $ManifestPath" -ForegroundColor DarkGray
            return 0
        }
        if ($PreviousManagedState.ContainsKey($ManifestPath) -and $PreviousManagedState[$ManifestPath] -eq $targetHash) {
            if ($PSCmdlet.ShouldProcess($To, "Refresh managed bundle asset")) {
                Copy-Item $From $To -Force
                $ManagedState[$ManifestPath] = $sourceHash
                Write-Host "  OK (refreshed managed): $ManifestPath" -ForegroundColor Green
                return 1
            }
            return 0
        }
        Write-Host "  SKIP (exists): $ManifestPath - use -Force to overwrite" -ForegroundColor Yellow
        return 0
    }
    if ($PSCmdlet.ShouldProcess($To, "Copy bundle asset")) {
        Copy-Item $From $To -Force
        $ManagedState[$ManifestPath] = $sourceHash
        Write-Host "  OK: $ManifestPath" -ForegroundColor Green
        return 1
    }
    return 0
}

function Write-ManagedRuntimeTargets {
    param([hashtable]$ManagedState)

    $installStateDir = Split-Path -Parent $RuntimeInstallStatePath
    if (-not (Test-Path $installStateDir)) {
        if ($PSCmdlet.ShouldProcess($installStateDir, "Create install-state directory")) {
            New-Item -ItemType Directory -Path $installStateDir -Force | Out-Null
        }
    }
    if ($PSCmdlet.ShouldProcess($RuntimeInstallStatePath, "Write runtime install state")) {
        $lines = foreach ($pathKey in ($ManagedState.Keys | Sort-Object)) {
            "$pathKey|$($ManagedState[$pathKey])"
        }
        ($lines -join "`n") | Set-Content -Path $RuntimeInstallStatePath -Encoding utf8
    }
}

$SharedRuntimeRoot = Resolve-DreamersMcpCheckout -CandidatePath $DreamersMcpPath
$SharedRuntimePackageSource = Join-Path $SharedRuntimeRoot "dreamers_stats"
$previousManagedState = Get-ManagedRuntimeState
$managedRuntimeState = @{}
$total = 0

Write-Host "`nDreamers MCP Copilot Bundle Installer" -ForegroundColor Cyan
Write-Host "Bundle:  $BundleSource"
Write-Host "Runtime: $SharedRuntimePackageSource"
Write-Host "Target:  $CopilotHome`n"

Write-Host "[dreamers/runtime]" -ForegroundColor Cyan
$total += Copy-Files -From $SharedRuntimePackageSource -To (Join-Path $CopilotHome "dreamers" "runtime" "dreamers_stats") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManagedPrefix "dreamers/runtime/dreamers_stats" -Recurse
$nodeRuntimeTarget = Join-Path $CopilotHome "dreamers" "runtime" "dreamers_mcp_node"
$total += Copy-ManagedFile -From (Join-Path $SharedRuntimeRoot "package.json") -To (Join-Path $nodeRuntimeTarget "package.json") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManifestPath "dreamers/runtime/dreamers_mcp_node/package.json"
$total += Copy-Files -From (Join-Path $SharedRuntimeRoot "dist") -To (Join-Path $nodeRuntimeTarget "dist") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManagedPrefix "dreamers/runtime/dreamers_mcp_node/dist" -Recurse

Write-Host "[dreamers/scripts]" -ForegroundColor Cyan
$total += Copy-Files -From ([System.IO.Path]::Combine($SharedRuntimeRoot, "bundles", "shared", "scripts")) -To (Join-Path $CopilotHome "dreamers" "scripts") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManagedPrefix "dreamers/scripts"
$total += Copy-Files -From (Join-Path $BundleSource "scripts") -To (Join-Path $CopilotHome "dreamers" "scripts") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManagedPrefix "dreamers/scripts"

Write-Host "[hooks]" -ForegroundColor Cyan
$total += Copy-Files -From (Join-Path $BundleSource "hooks") -To (Join-Path $CopilotHome "hooks") -PreviousManagedState $previousManagedState -ManagedState $managedRuntimeState -ManagedPrefix "hooks"

Write-ManagedRuntimeTargets -ManagedState $managedRuntimeState

Write-Host "`nInstalled $total bundle file(s).`n" -ForegroundColor Cyan
