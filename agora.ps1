<#
.SYNOPSIS
    One-command launcher for Agora on Windows.

.DESCRIPTION
    The backend (uvicorn) already serves the built frontend from frontend/out,
    so daily use is a single process. This script starts it in the background,
    waits for /health, and opens the browser.

    Commands:
      up        start the backend if it is not running, then open the browser
                  -Build      rebuild frontend/out first (also when already running:
                              the backend serves the files from disk, no restart needed)
                  -NoBrowser  do not open the browser
                  -InTab      open in a normal browser tab instead of an Edge app window
      down      stop the Agora backend on the port: first a normal close, then
                force after a grace period. Anything on the port that is not
                running agora.api.app is left alone.
      status    is it running, which pid, how fresh is the frontend build
      logs      show the last 40 lines of the backend log
      build     rebuild frontend/out (corepack pnpm build)
      shortcut  create "Agora.lnk" on the Desktop that runs "up"
      -Dev      with up/down/status: this checkout as a development install on port
                8001, with config.dev.yaml (own database, a sandbox project instead of
                this repository) and its own keyring namespace. Never the stable data.
      upgrade   (stable install only) move this install to a release tag:
                  -To v1.2.0   the tag. Backs the database up first; any failure
                               puts the old version and the old database back.
      rollback  (stable install only) go back to the version before the last
                upgrade, restoring the database taken then. Needs -Yes.

    Which install is this? "stable" only when the code is exactly a v* release tag
    with a clean tree; anything else is "dev". The backend is told (AGORA_INSTANCE)
    and the top bar shows it. This launcher only ever touches the Agora started
    from THIS directory: another checkout's server on the same port is left alone.

    Runtime state lives in .agora/launcher/ (pid file and logs per port; gitignored).

.EXAMPLE
    .\agora.ps1 up
    .\agora.ps1 down
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'logs', 'build', 'shortcut', 'upgrade', 'rollback', 'help')]
    [string]$Command = 'help',
    [switch]$Build,
    [switch]$NoBrowser,
    [switch]$InTab,
    [switch]$Pause,
    [int]$Port = 8000,
    [int]$StartTimeoutSec = 60,
    [int]$StopGraceSec = 10,
    [string]$To,
    [switch]$Yes,
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'

# -Dev: this checkout as a development install (CR-31 slice 2): config.dev.yaml (its own
# database and sandbox project), its own keyring namespace, port 8001 unless -Port says.
if ($Dev -and -not $PSBoundParameters.ContainsKey('Port')) { $Port = 8001 }
$ConfigName = 'config.yaml'
if ($Dev) { $ConfigName = 'config.dev.yaml' }

$Root      = $PSScriptRoot
$Backend   = Join-Path $Root 'backend'
$Frontend  = Join-Path $Root 'frontend'
$OutIndex  = Join-Path $Frontend 'out\index.html'
$StateDir  = Join-Path $Root '.agora\launcher'
$PidFile   = Join-Path $StateDir "api-$Port.pid"
$OutLog    = Join-Path $StateDir "api-$Port.out.log"
$ErrLog    = Join-Path $StateDir "api-$Port.err.log"
$Url       = "http://127.0.0.1:$Port"
$AppModule = 'agora.api.app:app'
$JournalFile = Join-Path $StateDir 'upgrade-journal.json'
$HistoryFile = Join-Path $StateDir 'release-history.jsonl'

function Write-Step([string]$Message) { Write-Host "[agora] $Message" }
function Write-Fail([string]$Message) { Write-Host "[agora] $Message" -ForegroundColor Red }

function Get-Health {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/health" -TimeoutSec 2
        if ($r.StatusCode -eq 200) { return ($r.Content | ConvertFrom-Json) }
    } catch { }
    return $null
}

function Get-Listener {
    # The process that owns the listening socket on $Port, or $null.
    # Only sockets that actually block 127.0.0.1:$Port count: loopback itself
    # and the wildcard binds. A server on another interface's :$Port is none of
    # this launcher's business.
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') } |
            Select-Object -First 1
    } catch { return $null }
    if ($null -eq $conn) { return $null }
    return Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
}

function Get-CommandLine([int]$ProcessId) {
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($null -ne $p) { return [string]$p.CommandLine }
    } catch { }
    return ''
}

function Test-IsDescendantOf([int]$ProcessId, [int]$AncestorId) {
    # True when $ProcessId is $AncestorId or one of its children within a few
    # levels: the uvicorn.exe stub and 'uv run' both put the real server one or
    # two processes below the pid this launcher holds.
    $current = $ProcessId
    for ($i = 0; $i -lt 4; $i++) {
        if ($current -eq $AncestorId) { return $true }
        try {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction Stop
        } catch { return $false }
        if ($null -eq $p -or $p.ParentProcessId -le 0) { return $false }
        $current = [int]$p.ParentProcessId
    }
    return $false
}

function Test-IsAgoraProcess([int]$ProcessId) {
    # "Ours" means: it is running the Agora API module on this port, FROM THIS
    # DIRECTORY (its command line names this checkout's backend). The stable
    # install and a development checkout run side by side; 'down' in one must
    # never stop the other (CR-31). Anything else is a stranger.
    $cmd = Get-CommandLine $ProcessId
    $here = $cmd.IndexOf("$Backend\", [StringComparison]::OrdinalIgnoreCase) -ge 0
    return ($here -and ($cmd -like "*$AppModule*") -and ($cmd -like "*--port*$Port*"))
}

function Test-IsOtherAgora([int]$ProcessId) {
    # Agora on this port, but started from another directory.
    $cmd = Get-CommandLine $ProcessId
    return (($cmd -like "*$AppModule*") -and -not (Test-IsAgoraProcess $ProcessId))
}

function Invoke-Tool([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $Root) {
    # A native tool whose stderr must not become a terminating error (git prints
    # progress there). Returns @{ Code; Output }.
    $ErrorActionPreference = 'Continue'
    Push-Location $WorkDir
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    return @{ Code = $code; Output = (@($out) -join "`n") }
}

function Set-OwnConfig {
    # Always this directory's config.yaml, so this install's database. Pinned, not
    # just cleared: the backend reads .env again with setdefault, so a copied .env
    # naming another install's config would otherwise win (CR-31 review).
    # Both modes set the namespace explicitly: a value left in this shell by an earlier
    # '-Dev' run must not carry into a normal start (review).
    $configName = $ConfigName
    $keyring = 'agora-remote-env'
    if ($Dev) { $keyring = 'agora-remote-env-dev' }
    [Environment]::SetEnvironmentVariable('AGORA_KEYRING_SERVICE', $keyring, 'Process')
    [Environment]::SetEnvironmentVariable('AGORA_CONFIG_PATH', (Join-Path $Root $configName), 'Process')
}

function Get-InstallProcesses {
    # Every Agora server started from THIS directory, on any port: an upgrade or
    # a restore must not run while one of them still uses this code and database.
    $needle = "$Backend\"
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cmd = [string]$_.CommandLine
        $cmd.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $cmd -like "*$AppModule*"
    })
}

function Stop-Install {
    Do-Down
    foreach ($p in Get-InstallProcesses) {
        Write-Step "Stopping this install's server on another port (pid $($p.ProcessId)) ..."
        [void](Stop-Tree ([int]$p.ProcessId))
    }
    $left = Get-InstallProcesses
    if ($left.Count -gt 0) { throw "Agora from $Root is still running (pid $(($left | ForEach-Object { $_.ProcessId }) -join ', ')); stop it first." }
}

function Assert-BackupPresent($Entry) {
    # A recorded backup that is gone is not "no backup": restoring nothing and then
    # starting older code on a newer database is the one outcome to refuse.
    if ($Entry.backup -and -not (Test-Path $Entry.backup)) {
        throw "The database backup recorded for this upgrade is missing: $($Entry.backup). Nothing was changed."
    }
}

function Get-InstanceKind {
    # 'stable' only when HEAD is exactly a v* tag and nothing differs from it.
    # Wrongly saying 'dev' about a release is harmless; the reverse is not.
    # A -Dev start is a development install whatever the code is: right after a release
    # the checkout sits exactly on the new tag, and the sandbox then called itself the
    # stable install - the very confusion between 8000 and 8001 the badge exists to
    # prevent (CR-60).
    if ($Dev) { return 'dev' }
    $tag = Invoke-Tool 'git' @('describe', '--exact-match', '--tags', '--match', 'v*', 'HEAD')
    if ($tag.Code -ne 0) { return 'dev' }
    $dirty = Invoke-Tool 'git' @('status', '--porcelain')
    if ($dirty.Code -ne 0 -or $dirty.Output.Trim()) { return 'dev' }
    return 'stable'
}

function Read-LauncherPid {
    # The pid this launcher wrote, but only if that pid is still alive AND still
    # an Agora process on this port. Windows reuses pids: a stale file may point
    # at an editor by now, and that must never be a kill target.
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = Get-Content $PidFile -TotalCount 1 -ErrorAction SilentlyContinue
    if ($null -eq $raw) { return $null }
    $parsed = 0
    if (-not [int]::TryParse(([string]$raw).Trim(), [ref]$parsed)) { return $null }
    $proc = Get-Process -Id $parsed -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    if (-not (Test-IsAgoraProcess $parsed)) { return $null }
    return $parsed
}

function Get-RunningAgora {
    # Returns @{ Health; Listener } when an Agora process on $Port answers
    # /health; $null otherwise. A 200 on /health alone is not enough: another
    # dev server on the port could answer it too.
    $health = Get-Health
    if ($null -eq $health) { return $null }
    $listener = Get-Listener
    if ($null -eq $listener) { return $null }
    if (-not (Test-IsAgoraProcess $listener.Id)) { return $null }
    return @{ Health = $health; Listener = $listener }
}

function Get-Runner {
    # Prefer the project venv (works without uv on PATH); fall back to uv.
    $venvUvicorn = Join-Path $Backend '.venv\Scripts\uvicorn.exe'
    if (Test-Path $venvUvicorn) {
        return @{ Exe = $venvUvicorn; Args = @($AppModule, '--host', '127.0.0.1', '--port', "$Port"); Label = 'backend\.venv' }
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uv) {
        return @{ Exe = $uv.Source; Args = @('run', 'uvicorn', $AppModule, '--host', '127.0.0.1', '--port', "$Port"); Label = 'uv run' }
    }
    throw "No backend runtime found. Neither backend\.venv\Scripts\uvicorn.exe exists nor is 'uv' on PATH. Run: cd backend; uv sync --locked --extra dev"
}

function Import-DotEnv {
    # The plain subset of docker-compose's env_file: KEY=VALUE lines, '#'
    # comments, an unquoted value ends at ' #', quotes are stripped. No ${VAR}
    # interpolation and no escape processing inside quotes: a value is passed
    # exactly as written. Variables already set in this shell win.
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) { return }
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $eq = $t.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $t.Substring(0, $eq).Trim()
        $val = $t.Substring($eq + 1).Trim()
        if ($val.Length -ge 2 -and (($val[0] -eq '"' -and $val[-1] -eq '"') -or ($val[0] -eq "'" -and $val[-1] -eq "'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        } else {
            $hash = $val.IndexOf(' #')
            if ($hash -ge 0) { $val = $val.Substring(0, $hash).Trim() }
        }
        if ($null -eq [Environment]::GetEnvironmentVariable($key, 'Process')) {
            [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
}

function Invoke-FrontendBuild {
    $corepack = Get-Command corepack -ErrorAction SilentlyContinue
    if ($null -eq $corepack) { throw "corepack not found (Node.js is required to build the frontend). Install Node.js 20+ and retry." }
    Write-Step 'Building frontend (corepack pnpm build) ...'
    Push-Location $Frontend
    try {
        # Through cmd, merging stderr there: if the caller redirects this
        # script's stderr (".\agora.ps1 up 2>&1 | tee ..."), PowerShell 5.1
        # would otherwise turn pnpm's stderr chatter into a terminating error
        # under ErrorActionPreference=Stop and abort a build that is fine.
        # cmd resolves 'corepack' itself (to corepack.cmd/.exe): PowerShell may
        # have resolved it to a corepack.ps1 shim, which cmd cannot run.
        & cmd.exe /c "corepack pnpm build 2>&1"
        $buildExit = $LASTEXITCODE
        if ($buildExit -ne 0) { throw "Frontend build failed (exit $buildExit)." }
    } finally { Pop-Location }
    if (-not (Test-Path $OutIndex)) { throw "Frontend build finished but $OutIndex is missing." }
    Write-Step 'Frontend built.'
}

function Get-TerminalToken {
    # The install's terminal token (CR-30), from the file only this account can read.
    # Opening the browser through /api/terminal-auth turns it into a cookie, so the
    # person who started Agora can open terminals and nobody else on the machine can.
    $python = Join-Path $Backend '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { return $null }
    Set-OwnConfig
    $code = 'from agora.config.settings import get_config, resolve_control_plane_db_path; from agora.terminal.auth import load_or_create_token; print(load_or_create_token(resolve_control_plane_db_path(get_config()).parent))'
    $r = Invoke-Tool $python @('-c', $code) $Backend
    if ($r.Code -ne 0) { Write-Fail "Terminal token unavailable; terminals stay locked: $($r.Output)"; return $null }
    return (($r.Output -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
}

function Open-Browser {
    if ($NoBrowser) { return }
    $target = $Url
    $token = Get-TerminalToken
    if ($token) {
        # The token goes in a header, never a URL (URLs reach access logs and history):
        # it buys a one-time, 60-second launch code, and that is what the browser opens.
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Url/api/terminal-launch-code" -Headers @{ 'X-Agora-Terminal-Token' = $token } -TimeoutSec 5
            $code = ($r.Content | ConvertFrom-Json).code
            if ($code) { $target = "$Url/?terminal_code=$([uri]::EscapeDataString($code))" }
        } catch { Write-Fail "No terminal launch code ($($_.Exception.Message)); terminals stay locked in this browser." }
    }
    # An app window of its own (CR-64): its own taskbar icon, no address bar or tab strip,
    # so Agora is not one tab among many. Edge ships with Windows; without it, or with
    # -InTab, the default browser opens a tab as before.
    $edge = $null
    if (-not $InTab) {
        # Only from the variables that exist: Join-Path on a missing one throws, and with
        # ErrorActionPreference=Stop that would end the launch with no browser at all.
        $edge = @(${env:ProgramFiles(x86)}, $env:ProgramFiles) |
            Where-Object { $_ } |
            ForEach-Object { Join-Path $_ 'Microsoft\Edge\Application\msedge.exe' } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if ($edge) {
        Start-Process -FilePath $edge -ArgumentList "--app=$target" | Out-Null
    } else {
        Start-Process $target | Out-Null
    }
}

function Show-LogTail([int]$Lines = 30) {
    foreach ($f in @($ErrLog, $OutLog)) {
        if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) {
            Write-Host "---- $f (last $Lines lines) ----"
            Get-Content $f -Tail $Lines
        }
    }
}

function Initialize-DevSandbox {
    # The development install's one project (config.dev.yaml) must be a git repository of
    # its own, or provisioning a worktree for it fails (review). Never this checkout.
    $sandbox = Join-Path $Root '.agora\dev\sandbox'
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
    # Done means a commit exists, not that .git does: an init whose commit failed is
    # resumed rather than left unborn (review).
    if ((Test-Path (Join-Path $sandbox '.git')) -and (Invoke-Tool 'git' @('rev-parse', '--verify', '-q', 'HEAD') $sandbox).Code -eq 0) { return }
    Set-Content -Path (Join-Path $sandbox 'README.md') -Value '# Agora development sandbox' -Encoding ascii
    foreach ($a in @(@('init', '-q'), @('add', 'README.md'), @('-c', 'user.name=agora', '-c', 'user.email=agora@localhost', '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'sandbox'))) {
        $r = Invoke-Tool 'git' $a $sandbox
        if ($r.Code -ne 0) { throw "Could not initialise the development sandbox ($sandbox): $($r.Output)" }
    }
    Write-Step "Initialised the development sandbox repository at $sandbox."
}

function Do-Up {
    # While an upgrade is unfinished, which code may meet which database is the
    # journal's decision: an ordinary start (the desktop shortcut) could run new
    # code on the old database behind recovery's back (CR-31 review).
    if ((Test-Path $JournalFile) -and -not $script:Recovering) {
        throw "An upgrade did not finish ($JournalFile). Run '.\agora.ps1 rollback' to recover before starting Agora."
    }
    $running = Get-RunningAgora
    # A server that does not say its configuration (older than this launcher) is only
    # reused for a normal start; the development install must be positively itself (review).
    $mismatch = $running.Health.config -and $running.Health.config -ne $ConfigName
    $unknownForDev = $Dev -and -not $running.Health.config
    if ($null -ne $running -and ($mismatch -or $unknownForDev)) {
        $said = $running.Health.config
        if (-not $said) { $said = 'an unstated configuration' }
        throw "The Agora on port $Port runs with $said, not $ConfigName. Stop it ('.\agora.ps1 down -Port $Port') or pick another port."
    }
    if ($null -ne $running) {
        Write-Step "Already running at $Url (pid $($running.Listener.Id), version $($running.Health.version))."
        if ($Build) { Invoke-FrontendBuild }
        Open-Browser
        return
    }

    $holder = Get-Listener
    if ($null -ne $holder) {
        if (Test-IsAgoraProcess $holder.Id) {
            throw "Port $Port is held by an Agora process (pid $($holder.Id)) that does not answer /health. Run '.\agora.ps1 down' and try again, or check '.\agora.ps1 logs'."
        }
        if (Test-IsOtherAgora $holder.Id) {
            throw "Port $Port is held by another Agora install (pid $($holder.Id)): $(Get-CommandLine $holder.Id). This launcher only manages the one in $Root. Use -Port <other>, or stop it from its own directory."
        }
        throw "Port $Port is held by pid $($holder.Id) ($($holder.ProcessName)), which is not Agora. Stop it or use -Port <other>."
    }

    $earlier = Read-LauncherPid
    if ($null -ne $earlier) {
        throw "A backend started by this launcher (pid $earlier) is alive but not listening on $Port yet (still starting, or stuck). Wait a moment, check '.\agora.ps1 logs', or run '.\agora.ps1 down' before starting another."
    }

    $runner = Get-Runner
    if ($Dev) { Initialize-DevSandbox }

    if ($Build -or -not (Test-Path $OutIndex)) {
        if (-not $Build) { Write-Step "frontend\out is missing; building it once." }
        Invoke-FrontendBuild
    }

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Import-DotEnv
    Set-OwnConfig
    $instance = Get-InstanceKind
    [Environment]::SetEnvironmentVariable('AGORA_INSTANCE', $instance, 'Process')

    Write-Step "Starting the $instance install from $Root via $($runner.Label) on $Url ..."
    $proc = Start-Process -FilePath $runner.Exe -ArgumentList $runner.Args -WorkingDirectory $Backend `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    try {
        Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii
    } catch {
        # No record of it means no way to stop it later: do not leave it running.
        Invoke-Taskkill "/PID $($proc.Id) /T /F" | Out-Null
        throw "Started the backend (pid $($proc.Id)) but could not write $PidFile ($($_.Exception.Message)); stopped it again."
    }

    $deadline = (Get-Date).AddSeconds($StartTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if ($proc.HasExited) {
            Show-LogTail
            throw "Backend exited during startup (exit code $($proc.ExitCode)). See the log above."
        }
        $health = Get-Health
        if ($null -ne $health) {
            # The answer must come from the process we just started (or its
            # child), not from another instance that won the port meanwhile.
            $listener = Get-Listener
            if ($null -ne $listener -and (Test-IsDescendantOf $listener.Id $proc.Id)) {
                Write-Step "Agora is up at $Url (pid $($proc.Id), version $($health.version), $($health.instance))."
                Open-Browser
                return
            }
            if ($null -ne $listener) {
                Invoke-Taskkill "/PID $($proc.Id) /T /F" | Out-Null
                if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
                throw "Port $Port answered /health, but from pid $($listener.Id) ($($listener.ProcessName)), not from the backend just started (pid $($proc.Id)). Stopped ours; another instance owns the port."
            }
        }
    }
    Show-LogTail
    throw "Backend did not answer /health within $StartTimeoutSec s. It is still running (pid $($proc.Id)); check the log above or run '.\agora.ps1 down'."
}

function Invoke-Taskkill([string]$Arguments) {
    # taskkill writes its complaints to stderr; under ErrorActionPreference=Stop
    # a native stderr line would become a terminating error, so run it through
    # cmd and keep only the exit code.
    & cmd.exe /c "taskkill.exe $Arguments >nul 2>&1"
    return $LASTEXITCODE
}

function Send-CtrlBreak([int]$ProcessId) {
    # The backend runs in a hidden console, so taskkill without /F has no window
    # to close and cannot stop it politely. What uvicorn does handle on Windows
    # is Ctrl+Break (SIGBREAK): a helper attaches to the target's console and
    # raises that event, which triggers uvicorn's normal shutdown. Returns $true
    # when the event was delivered.
    $python = Join-Path $Backend '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $cmd) { return $false }
        $python = $cmd.Source
    }
    # The event reaches every process on that console, the helper included, so
    # the helper installs a handler that swallows it before raising it. The
    # helper is written to a file: quoting a -c one-liner through Start-Process
    # is exactly the kind of thing that breaks silently.
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $helperPath = Join-Path $StateDir 'ctrlbreak.py'
    $helper = @(
        'import ctypes, sys',
        'k = ctypes.windll.kernel32',
        'pid = int(sys.argv[1])',
        'HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)',
        'swallow = HANDLER(lambda kind: 1)',
        'k.FreeConsole()',
        'if not k.AttachConsole(pid):',
        '    sys.exit(2)',
        'k.SetConsoleCtrlHandler(swallow, True)',
        'ok = k.GenerateConsoleCtrlEvent(1, 0)',
        'k.FreeConsole()',
        'sys.exit(0 if ok else 3)'
    )
    Set-Content -Path $helperPath -Value $helper -Encoding ascii
    $p = Start-Process -FilePath $python -ArgumentList @("`"$helperPath`"", "$ProcessId") -WindowStyle Hidden -PassThru -Wait
    # 0: raised and survived. -1073741510 (0xC000013A, STATUS_CONTROL_C_EXIT): the
    # helper was itself terminated by the event it raised, which is delivery
    # proven the hard way (measured: uvicorn logs its normal shutdown in both
    # cases). 2: AttachConsole failed, nothing was delivered.
    return ($p.ExitCode -eq 0 -or $p.ExitCode -eq -1073741510)
}

function Stop-Tree([int]$ProcessId) {
    # Polite first (Ctrl+Break -> uvicorn's own shutdown, which lets in-flight
    # requests finish), force after the grace period. /T takes the children
    # too: 'uv run' and the uvicorn.exe stub both spawn the real server as a
    # child. Returns $true when the process is gone.
    # Everything that must be gone afterwards: the pid itself, its children
    # (found now, before anything exits and the parent link is lost), and the
    # listener. A stuck child that stopped listening would otherwise be
    # invisible to both the port check and a taskkill on a parent that has
    # already exited.
    $family = @($ProcessId)
    try {
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction Stop |
            ForEach-Object { $family += [int]$_.ProcessId }
    } catch { }
    $listener = Get-Listener
    $target = $ProcessId
    if ($null -ne $listener -and (Test-IsAgoraProcess $listener.Id)) {
        $target = $listener.Id
        if ($family -notcontains $target) { $family += $target }
    }

    $delivered = $false
    try { $delivered = Send-CtrlBreak $target } catch { Write-Step "Ctrl+Break helper failed ($($_.Exception.Message))." }
    if ($delivered) {
        $deadline = (Get-Date).AddSeconds($StopGraceSec)
        while ((Get-Date) -lt $deadline) {
            $alive = @($family | Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
            if ($alive.Count -eq 0) { return $true }
            Start-Sleep -Milliseconds 300
        }
        Write-Step "pid $ProcessId did not exit within $StopGraceSec s after Ctrl+Break; forcing."
    } else {
        Write-Step 'Could not deliver Ctrl+Break; forcing.'
    }
    foreach ($id in $family) {
        if ($null -ne (Get-Process -Id $id -ErrorAction SilentlyContinue)) { Invoke-Taskkill "/PID $id /T /F" | Out-Null }
    }
    Start-Sleep -Milliseconds 500
    $left = @($family | Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
    if ($left.Count -eq 0) { return $true }
    Write-Fail "Forced stop, but pid(s) $($left -join ', ') are still alive (access denied?)."
    return $false
}

function Do-Down {
    $targets = @()
    $launcherPid = Read-LauncherPid
    if ($null -ne $launcherPid) { $targets += @{ Id = $launcherPid; Why = 'started by this launcher' } }

    $holder = Get-Listener
    if ($null -ne $holder) {
        if (Test-IsAgoraProcess $holder.Id) {
            if ($null -eq $launcherPid) { $targets += @{ Id = $holder.Id; Why = "listening on port $Port" } }
        } elseif (Test-IsOtherAgora $holder.Id) {
            Write-Fail "Port $Port is held by another Agora install (pid $($holder.Id)), not the one in $Root. Leaving it alone."
        } else {
            Write-Fail "Port $Port is held by pid $($holder.Id) ($($holder.ProcessName)), which is not Agora. Leaving it alone."
        }
    }

    if ($targets.Count -eq 0) {
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
        Write-Step 'Agora is not running.'
        return
    }

    $allGone = $true
    foreach ($t in $targets) {
        Write-Step "Stopping Agora backend (pid $($t.Id), $($t.Why)) ..."
        if (-not (Stop-Tree $t.Id)) { $allGone = $false }
    }
    if (-not $allGone) { throw "Could not stop Agora. The pid file is kept; check Task Manager." }

    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and $null -ne (Get-Listener)) { Start-Sleep -Milliseconds 300 }
    $still = Get-Listener
    if ($null -ne $still -and (Test-IsAgoraProcess $still.Id)) {
        throw "Processes were stopped but pid $($still.Id) still holds port $Port. Run down again."
    }
    Write-Step 'Agora stopped.'
}

function Do-Status {
    $running = Get-RunningAgora
    $holder = Get-Listener
    if ($null -ne $running) {
        Write-Step "RUNNING at $Url (pid $($running.Listener.Id), version $($running.Health.version), $($running.Health.instance))."
    } elseif ($null -ne $holder) {
        if (Test-IsAgoraProcess $holder.Id) {
            Write-Fail "Port $Port is held by an Agora process (pid $($holder.Id)) that does not answer /health (starting, or stuck)."
        } elseif (Test-IsOtherAgora $holder.Id) {
            Write-Step "NOT RUNNING from $Root. Port $Port is used by another Agora install (pid $($holder.Id))."
        } else {
            Write-Fail "NOT RUNNING. Port $Port is held by pid $($holder.Id) ($($holder.ProcessName)), not Agora."
        }
    } else {
        Write-Step 'NOT RUNNING.'
    }
    $launcherPid = Read-LauncherPid
    if ($null -ne $launcherPid) {
        Write-Step "Launcher pid file: $launcherPid (alive)."
    } elseif (Test-Path $PidFile) {
        Write-Step "Launcher pid file is stale ($PidFile); 'down' will clean it up."
    }
    if (Test-Path $OutIndex) {
        $built = (Get-Item $OutIndex).LastWriteTime
        Write-Step "Frontend build: $($built.ToString('yyyy-MM-dd HH:mm'))."
        # Everything the build reads (src, public, next.config, package.json ...)
        # minus what it writes and installs, and minus tsc's incremental cache
        # (`tsconfig.tsbuildinfo`): `tsc --noEmit` rewrites it on every type-check,
        # which is not a change the build would pick up. A deleted source file
        # leaves no timestamp behind, so this can only say "stale", never "fresh".
        $skip = '\\node_modules\\|\\out\\|\\\.next\\|\.tsbuildinfo$'
        $newer = Get-ChildItem $Frontend -Recurse -File |
            Where-Object { $_.FullName -notmatch $skip -and $_.LastWriteTime -gt $built } |
            Select-Object -First 1
        if ($null -ne $newer) { Write-Step "  frontend has changes newer than the build (e.g. $($newer.Name)); run '.\agora.ps1 build' to pick them up." }
        else { Write-Step "  no frontend file is newer than the build (deletions are not detected)." }
    } else {
        Write-Fail "Frontend build missing ($OutIndex). 'up' will build it."
    }
    try { $runner = Get-Runner; Write-Step "Backend runtime: $($runner.Label)." } catch { Write-Fail $_.Exception.Message }
}

function Do-Logs {
    if (-not (Test-Path $ErrLog) -and -not (Test-Path $OutLog)) {
        Write-Step "No logs yet ($StateDir)."
        return
    }
    Show-LogTail 40
}

function Do-Shortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desktop 'Agora.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $scriptPath = Join-Path $Root 'agora.ps1'
    if (Test-Path $lnk) {
        # Only a shortcut this command made gets rewritten: powershell.exe running
        # exactly this script via -File. Anything else on that name is someone
        # else's and stays untouched.
        $existing = $shell.CreateShortcut($lnk)
        $isOurs = (([string]$existing.TargetPath) -like '*\powershell.exe' -or [string]$existing.TargetPath -eq 'powershell.exe') -and
                  (([string]$existing.Arguments) -like "*-File `"*\agora.ps1`" up*")
        if (-not $isOurs) {
            throw "$lnk already exists and points somewhere else (target: $($existing.TargetPath) $($existing.Arguments)). Remove or rename it first."
        }
        Write-Step "Updating existing $lnk"
    }
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = 'powershell.exe'
    $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" up -Pause"
    $sc.WorkingDirectory = $Root
    $icon = Join-Path $Frontend 'src\app\favicon.ico'
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Description = 'Start Agora and open it in the browser'
    $sc.Save()
    Write-Step "Created $lnk"
}

function Get-CodeVersion {
    $init = Get-Content (Join-Path $Backend 'agora\__init__.py') -Raw
    if ($init -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    throw "Could not read __version__ from backend\agora\__init__.py."
}

function Get-DatabasePath {
    # Asked of Agora itself, so this is the file the server really uses.
    $python = Join-Path $Backend '.venv\Scripts\python.exe'
    $code = 'from agora.config.settings import get_config, resolve_control_plane_db_path; print(resolve_control_plane_db_path(get_config()))'
    Set-OwnConfig
    $r = Invoke-Tool $python @('-c', $code) $Backend
    if ($r.Code -ne 0) { throw "Could not resolve the database path: $($r.Output)" }
    return (($r.Output -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
}

function Backup-Database([string]$Database, [string]$Backup) {
    # SQLite's own backup API: a consistent copy including anything still in the
    # WAL, then an integrity check of the copy. A failed check is a failed backup.
    New-Item -ItemType Directory -Force -Path (Split-Path $Backup) | Out-Null
    $python = Join-Path $Backend '.venv\Scripts\python.exe'
    # No double quotes in the code: Windows PowerShell 5.1 strips them when it
    # passes arguments to a native program (review, CR-31).
    $code = 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); r=d.execute(''pragma integrity_check'').fetchone()[0]; d.close(); s.close(); print(r); sys.exit(0 if r==''ok'' else 3)'
    $r = Invoke-Tool $python @('-c', $code, $Database, $Backup) $Backend
    if ($r.Code -ne 0) { throw "Database backup failed or did not verify: $($r.Output)" }
    Write-Step "Database backed up to $Backup (integrity ok)."
}

function Restore-Database([string]$Database, [string]$Backup, [string]$KeepAs) {
    # The current file (and its -wal/-shm) is kept beside it, never deleted, then
    # the backup takes its place with no stale WAL left to replay over it.
    Move-DatabaseAside $Database $KeepAs
    Copy-Item $Backup $Database
    Write-Step "Database restored from $Backup (the replaced one is kept as $KeepAs)."
}

function Sync-Dependencies {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uv) { throw "'uv' is not on PATH; it installs the backend's locked dependencies." }
    Write-Step 'Installing backend dependencies (uv sync --locked) ...'
    $r = Invoke-Tool $uv.Source @('sync', '--locked') $Backend
    if ($r.Code -ne 0) { throw "uv sync failed: $($r.Output)" }
    Write-Step 'Installing frontend dependencies (pnpm install --frozen-lockfile) ...'
    $r = Invoke-Tool 'cmd.exe' @('/c', 'corepack pnpm install --frozen-lockfile') $Frontend
    if ($r.Code -ne 0) { throw "pnpm install failed: $($r.Output)" }
}

function Save-Journal($Journal) {
    # Written beside it and swapped in, so an interruption leaves the previous
    # valid journal rather than half of a new one (CR-31 review).
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $tmp = "$JournalFile.tmp"
    $Journal | ConvertTo-Json | Set-Content -Path $tmp -Encoding UTF8
    if (Test-Path $JournalFile) { [IO.File]::Replace($tmp, $JournalFile, [NullString]::Value) }  # a plain $null arrives as '' on PS 5.1
    else { [IO.File]::Move($tmp, $JournalFile) }
}

function Move-DatabaseAside([string]$Database, [string]$KeepAs) {
    foreach ($suffix in @('', '-wal', '-shm')) {
        $f = "$Database$suffix"
        if (Test-Path $f) { Move-Item -Force $f "$KeepAs$suffix" }
    }
}

function Complete-Upgrade($Journal) {
    # History first, journal last: an interruption in between leaves a journal
    # marked 'done', which this finishes again instead of rolling anything back.
    $recorded = $false
    if (Test-Path $HistoryFile) {
        $recorded = @(Get-Content $HistoryFile | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.action -eq 'upgrade' -and $_.started -eq $Journal.started }).Count -gt 0
    }
    if (-not $recorded) {
        Add-History ([ordered]@{ at = (Get-Date).ToString('o'); action = 'upgrade'; started = $Journal.started; from = $Journal.from_version; from_commit = $Journal.from_commit; to = $Journal.to; database = $Journal.database; backup = $Journal.backup })
    }
    Remove-Item $JournalFile -Force
}

function Add-History($Entry) {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Add-Content -Path $HistoryFile -Value ($Entry | ConvertTo-Json -Compress) -Encoding UTF8
}

function Start-AndVerify([string]$Version) {
    $script:NoBrowser = $true
    $script:Recovering = $true
    Do-Up
    $h = Get-Health
    if ($null -eq $h -or $h.version -ne $Version -or $h.instance -ne 'stable') {
        throw "After starting, /health says version '$($h.version)', install '$($h.instance)'; expected $Version, stable."
    }
}

function Move-Code([string]$Ref, [string]$Version) {
    $r = Invoke-Tool 'git' @('checkout', '--detach', $Ref)
    if ($r.Code -ne 0) { throw "git checkout $Ref failed: $($r.Output)" }
    Sync-Dependencies
    Invoke-FrontendBuild
    Start-AndVerify $Version
}

function Undo-Upgrade($Journal) {
    # Puts back exactly what THIS journal recorded: its commit, and its backup if
    # the new code may have touched the database (it was started).
    Write-Step "Rolling back to $($Journal.from_version) ($($Journal.from_commit)) ..."
    # From checkout on, new code was on disk and could have run (an upgrade's own
    # start, or anything started by hand), so the database goes back too.
    $touched = $Journal.phase -in @('checkout', 'dependencies', 'build', 'start')
    if ($touched) { Assert-BackupPresent $Journal }
    Stop-Install
    if ($touched) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $keep = "$($Journal.database).failed-$($Journal.to)-$stamp"
        if ($Journal.backup) { Restore-Database $Journal.database $Journal.backup $keep }
        else {
            # There was no database before; whatever the new code created goes aside.
            Move-DatabaseAside $Journal.database $keep
        }
    }
    Move-Code $Journal.from_commit $Journal.from_version
    Remove-Item $JournalFile -Force
    Add-History ([ordered]@{ at = (Get-Date).ToString('o'); action = 'upgrade-rolled-back'; from = $Journal.from_version; to = $Journal.to; backup = $Journal.backup })
}

function Assert-StableInstall {
    if ((Get-InstanceKind) -ne 'stable') {
        throw "This is not a stable install (HEAD is not exactly a v* tag with a clean tree): $Root. Upgrades and rollbacks only move the stable install."
    }
}

function Do-Upgrade {
    if (-not $To) { throw "upgrade needs -To <tag>, e.g. -To v1.2.0" }
    if (Test-Path $JournalFile) { throw "An earlier upgrade did not finish ($JournalFile). Run '.\agora.ps1 rollback' first." }
    Assert-StableInstall
    $fetch = Invoke-Tool 'git' @('fetch', 'origin', '--tags')
    if ($fetch.Code -ne 0) { throw "git fetch failed (nothing was changed): $($fetch.Output)" }
    $target = Invoke-Tool 'git' @('rev-parse', '--verify', '--quiet', "refs/tags/$To^{commit}")
    if ($target.Code -ne 0) { throw "There is no tag $To (nothing was changed)." }
    $journal = [ordered]@{
        started = (Get-Date).ToString('o')
        from_commit = (Invoke-Tool 'git' @('rev-parse', 'HEAD')).Output.Trim()
        from_version = Get-CodeVersion
        to = $To
        database = Get-DatabasePath
        backup = $null
        phase = 'stop'
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $journal.backup = Join-Path (Join-Path (Split-Path $journal.database) 'backups') "agora-$($journal.from_version)-before-$To-$stamp.db"
    # Written before anything stops, so every later failure knows what to restore.
    Save-Journal $journal
    try {
        Stop-Install
        $journal.phase = 'backup'; Save-Journal $journal
        if (Test-Path $journal.database) { Backup-Database $journal.database $journal.backup } else { $journal.backup = $null }
        $journal.phase = 'checkout'; Save-Journal $journal
        $r = Invoke-Tool 'git' @('checkout', '--detach', $To)
        if ($r.Code -ne 0) { throw "git checkout $To failed: $($r.Output)" }
        $journal.phase = 'dependencies'; Save-Journal $journal
        Sync-Dependencies
        $journal.phase = 'build'; Save-Journal $journal
        Invoke-FrontendBuild
        $journal.phase = 'start'; Save-Journal $journal
        Start-AndVerify ($To.TrimStart('v'))
    } catch {
        Write-Fail "Upgrade to $To failed during '$($journal.phase)': $($_.Exception.Message)"
        try { Undo-Upgrade $journal }
        catch { throw "Upgrade to $To failed, and rolling back failed too: $($_.Exception.Message). $JournalFile is kept; fix the cause and run '.gora.ps1 rollback -Yes'." }
        throw "Upgrade to $To failed and was rolled back to $($journal.from_version)."
    }
    $journal.phase = 'done'; Save-Journal $journal
    Complete-Upgrade $journal
    Write-Step "Upgraded $($journal.from_version) -> $To. Database backup: $($journal.backup)"
}

function Do-Rollback {
    if (Test-Path $JournalFile) {
        # An upgrade stopped half way (power cut, closed window): finish undoing it.
        $journal = Get-Content $JournalFile -Raw | ConvertFrom-Json
        if ($journal.phase -eq 'done') {
            # The upgrade itself finished; only its bookkeeping did not.
            Complete-Upgrade $journal
            Write-Step "Recorded the finished upgrade $($journal.from_version) -> $($journal.to). Run rollback again to undo it."
            return
        }
        Write-Fail "An upgrade $($journal.from_version) -> $($journal.to) stopped during '$($journal.phase)'. Rolling back returns to $($journal.from_version)."
        if ($journal.phase -in @('checkout', 'dependencies', 'build', 'start')) {
            Write-Fail "  * The database goes back to the backup taken before that upgrade; anything written since is lost (the current file is kept beside it)."
            Write-Fail "  * Remote environment passwords changed or deleted since then are NOT restored; re-enter them."
        }
        if (-not $Yes) { throw "Nothing was changed. Run '.\agora.ps1 rollback -Yes' to proceed." }
        Undo-Upgrade $journal
        return
    }
    Assert-StableInstall
    $last = $null
    if (Test-Path $HistoryFile) {
        # A failed upgrade that already undid itself changes nothing; the version
        # in use is still the one the last real upgrade or rollback left.
        $last = Get-Content $HistoryFile | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.action -in @('upgrade', 'rollback') } | Select-Object -Last 1
    }
    if ($null -eq $last) { throw "No upgrade is recorded in $HistoryFile; there is nothing to roll back to." }
    if ($last.action -ne 'upgrade') { throw "The last recorded action is '$($last.action)' ($($last.from) -> $($last.to)), not an upgrade; there is nothing to roll back." }
    Write-Fail "Rolling back $($last.to) -> $($last.from) restores the database backed up before that upgrade."
    Write-Fail "  * Everything written to Agora since $($last.to) was installed is lost (the current file is kept beside it)."
    Write-Fail "  * Remote environment passwords changed or deleted since then are NOT restored; re-enter them."
    Assert-BackupPresent $last
    if (-not $Yes) { throw "Nothing was changed. Run '.\agora.ps1 rollback -Yes' to proceed." }
    Stop-Install
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    if ($last.backup) {
        Restore-Database $last.database $last.backup "$($last.database).before-rollback-$stamp"
    } else {
        # That upgrade started with no database; the older code starts without one too.
        Move-DatabaseAside $last.database "$($last.database).before-rollback-$stamp"
        Write-Fail "That upgrade started with no database; the current one is kept as $($last.database).before-rollback-$stamp."
    }
    Move-Code $last.from_commit $last.from
    Add-History ([ordered]@{ at = (Get-Date).ToString('o'); action = 'rollback'; from = $last.to; to = $last.from; backup = $last.backup })
    Write-Step "Rolled back to $($last.from)."
}

function Do-Help {
    Get-Help $PSCommandPath -Detailed | Out-String | Write-Host
}

try {
    switch ($Command) {
        'up'       { Do-Up }
        'down'     { Do-Down }
        'status'   { Do-Status }
        'logs'     { Do-Logs }
        'build'    { Invoke-FrontendBuild }
        'shortcut' { Do-Shortcut }
        'upgrade'  { Do-Upgrade }
        'rollback' { Do-Rollback }
        default    { Do-Help }
    }
    $exit = 0
} catch {
    Write-Fail $_.Exception.Message
    $exit = 1
}

if ($Pause -and $exit -ne 0) {
    # Launched from the Desktop shortcut: keep the window open so the error is readable.
    [void](Read-Host 'Press Enter to close')
}
exit $exit
