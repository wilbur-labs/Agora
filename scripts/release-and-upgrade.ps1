<#
.SYNOPSIS
    Release a version, then move the stable install onto it, in one go.

.DESCRIPTION
    1. scripts/release.ps1 -Version X (every gate; commit, tag, push to origin)
    2. <stable install>\agora.ps1 upgrade -To vX (backup, checkout, build, start,
       verify; any failure rolls back)

    Run from the development checkout, on main, after the person has said to release.
    The upgrade runs as its own process writing to files: the backend it starts inherits
    the output handles, and a pipe would then never close (measured: the caller hangs).
    Do not run other test suites meanwhile - the backend gate is sensitive to load
    (CR-33).

.EXAMPLE
    .\scripts\release-and-upgrade.ps1 -Version 1.3.2
    .\scripts\release-and-upgrade.ps1 -Version 1.3.2 -Stable D:\Projects\Agora-stable
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$Stable = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'Agora-stable')
)
$ErrorActionPreference = 'Continue'
$Root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $Root '.agora\launcher'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "release-$Version.log"

if (-not (Test-Path (Join-Path $Stable 'agora.ps1'))) { throw "No stable install at $Stable (pass -Stable <dir>)." }

Write-Host "[release-and-upgrade] releasing v$Version (log: $log) ..."
& (Join-Path $PSScriptRoot 'release.ps1') -Version $Version *> $log
if (-not (Select-String -Path $log -Pattern "Released v$Version" -SimpleMatch -Quiet)) {
    Write-Host "[release-and-upgrade] release FAILED; nothing was installed. Last lines:"
    Get-Content $log -Tail 15
    exit 1
}

$out = Join-Path $logDir "upgrade-$Version.out.log"
$err = Join-Path $logDir "upgrade-$Version.err.log"
Write-Host "[release-and-upgrade] upgrading $Stable to v$Version ..."
$p = Start-Process powershell.exe -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + (Join-Path $Stable 'agora.ps1') + '"'), 'upgrade', '-To', "v$Version")
$p.WaitForExit()
Get-Content $out | Where-Object { $_ -match '^\[agora\]' } | Select-Object -Last 3
if ($p.ExitCode -ne 0) {
    # Whether the rollback itself succeeded is in the log, not in the exit code: say what to read.
    Write-Host "[release-and-upgrade] upgrade FAILED (exit $($p.ExitCode)). Read $out and $err to see whether it rolled back;"
    Write-Host "  if an upgrade journal is still there, run 'agora.ps1 rollback' in $Stable before using it."
    Get-Content $err -Tail 5
    exit $p.ExitCode
}
Write-Host "[release-and-upgrade] done: stable install is v$Version."
