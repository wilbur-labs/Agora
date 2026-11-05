<#
.SYNOPSIS
    Cut an Agora release: bump the version, run every gate on the result, tag, push.

.DESCRIPTION
    Run from a development checkout, on main, in sync with origin/main (CR-31).

      1. checks: on main, clean tree, HEAD == origin/main, the tag does not exist yet
      2. writes the version into its five records (backend/agora/__init__.py,
         backend/pyproject.toml, frontend/package.json, desktop/package.json, and
         backend/uv.lock via 'uv lock'); tests/test_version.py checks they agree
      3. runs every gate on exactly that content: backend pytest -m "not integration",
         frontend test:control-plane, lint, tsc --noEmit, build
      4. commits "release: vX.Y.Z", creates an annotated tag, pushes main and the
         tag to origin. Never to any other remote.

    Any failure before step 4 restores the version files and stops; nothing is pushed.
    Moving the stable install onto the new tag is a separate step, run there:
        .\agora.ps1 upgrade -To vX.Y.Z

.EXAMPLE
    .\scripts\release.ps1 -Version 1.2.0
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Tag = "v$Version"
$Files = @('backend/agora/__init__.py', 'backend/pyproject.toml', 'backend/uv.lock', 'frontend/package.json', 'desktop/package.json')

function Write-Step([string]$Message) { Write-Host "[release] $Message" }

function Invoke-Tool([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $Root) {
    $ErrorActionPreference = 'Continue'
    Push-Location $WorkDir
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    return @{ Code = $code; Output = (@($out) -join "`n") }
}

function Assert-Ok($Result, [string]$What) {
    if ($Result.Code -ne 0) {
        $tail = ($Result.Output -split "`n" | Select-Object -Last 30) -join "`n"
        throw "$What failed (exit $($Result.Code)):`n$tail"
    }
}

function Set-VersionIn([string]$Path, [string]$Pattern, [string]$Replacement) {
    $full = Join-Path $Root $Path
    $text = [IO.File]::ReadAllText($full)
    $regex = [regex]::new($Pattern, [Text.RegularExpressions.RegexOptions]::Multiline)
    if (-not $regex.IsMatch($text)) { throw "No version found in $Path" }
    [IO.File]::WriteAllText($full, $regex.Replace($text, $Replacement, 1), [Text.UTF8Encoding]::new($false))
}

# 1. Preconditions -------------------------------------------------------------
$branch = (Invoke-Tool 'git' @('rev-parse', '--abbrev-ref', 'HEAD')).Output.Trim()
if ($branch -ne 'main') { throw "Releases are cut from main; this checkout is on '$branch'." }
$dirty = Invoke-Tool 'git' @('status', '--porcelain')
if ($dirty.Output.Trim()) { throw "The tree has changes; commit or stash them first:`n$($dirty.Output)" }
Assert-Ok (Invoke-Tool 'git' @('fetch', 'origin', '--tags')) 'git fetch origin'
$head = (Invoke-Tool 'git' @('rev-parse', 'HEAD')).Output.Trim()
$remote = (Invoke-Tool 'git' @('rev-parse', 'origin/main')).Output.Trim()
if ($head -ne $remote) { throw "main ($head) is not origin/main ($remote). Pull or push first." }
if ((Invoke-Tool 'git' @('rev-parse', '--verify', '--quiet', "refs/tags/$Tag")).Code -eq 0) { throw "Tag $Tag already exists." }

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) { throw "'uv' is not on PATH; it updates backend/uv.lock." }

try {
    # 2. Version records ------------------------------------------------------
    Write-Step "Setting version $Version ..."
    Set-VersionIn 'backend/agora/__init__.py' '^__version__ = "[^"]+"' "__version__ = `"$Version`""
    Set-VersionIn 'backend/pyproject.toml' '^version = "[^"]+"' "version = `"$Version`""
    Set-VersionIn 'frontend/package.json' '^  "version": "[^"]+"' "  `"version`": `"$Version`""
    # The desktop shell (CR-72) ships with the release it opens; its installer is named by this.
    Set-VersionIn 'desktop/package.json' '^  "version": "[^"]+"' "  `"version`": `"$Version`""
    Assert-Ok (Invoke-Tool $uv.Source @('lock') $Backend) 'uv lock'

    # 3. Gates on exactly this content ----------------------------------------
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    # Frontend first: the backend suite checks the static export, which must be
    # this content's build, not an older one (AGENTS.md).
    foreach ($step in @('corepack pnpm test:control-plane', 'corepack pnpm lint', 'npx tsc --noEmit', 'corepack pnpm build')) {
        Write-Step "Frontend: $step ..."
        Assert-Ok (Invoke-Tool 'cmd.exe' @('/c', $step) $Frontend) "Frontend '$step'"
    }
    $python = Join-Path $Backend '.venv\Scripts\python.exe'
    Write-Step 'Backend tests (-m "not integration"; this takes a while) ...'
    Assert-Ok (Invoke-Tool $python @('-m', 'pytest', '-q', '-m', 'not integration', '-p', 'no:cacheprovider') $Backend) 'Backend tests'
    Write-Step 'Protocol schema consistency ...'
    Assert-Ok (Invoke-Tool $python @((Join-Path $Root 'scripts\export_protocol_schemas.py'), '--check') $Root) 'Schema check'

} catch {
    Write-Step 'Failed; restoring the version records. Nothing was committed or pushed.'
    Invoke-Tool 'git' (@('checkout', '--') + $Files) | Out-Null
    throw
}

# 4. Commit, tag, push (origin only) -------------------------------------------
Assert-Ok (Invoke-Tool 'git' (@('add', '--') + $Files)) 'git add'
# The commit review gate (CR-75 slice 2) would refuse it: the version files no reviewer
# read. Recorded as skipped with this reason, for this one commit only.
$priorSkip = $env:AGORA_REVIEW_SKIP
$env:AGORA_REVIEW_SKIP = 'release version bump'
try {
    Assert-Ok (Invoke-Tool 'git' @('commit', '-m', "release: $Tag")) 'git commit'
} finally {
    # Put back whatever the caller had (kiro review round 3).
    if ($null -eq $priorSkip) { Remove-Item env:AGORA_REVIEW_SKIP -ErrorAction SilentlyContinue }
    else { $env:AGORA_REVIEW_SKIP = $priorSkip }
}
Assert-Ok (Invoke-Tool 'git' @('tag', '-a', $Tag, '-m', "Agora $Tag")) 'git tag'
Assert-Ok (Invoke-Tool 'git' @('push', 'origin', 'main')) 'git push origin main'
Assert-Ok (Invoke-Tool 'git' @('push', 'origin', $Tag)) "git push origin $Tag"
Write-Step "Released $Tag. To install it: in the stable install run '.\agora.ps1 upgrade -To $Tag'."
