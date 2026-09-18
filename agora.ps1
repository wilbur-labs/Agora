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
      down      stop the Agora backend on the port: first a normal close, then
                force after a grace period. Anything on the port that is not
                running agora.api.app is left alone.
      status    is it running, which pid, how fresh is the frontend build
      logs      show the last 40 lines of the backend log
      build     rebuild frontend/out (corepack pnpm build)
      shortcut  create "Agora.lnk" on the Desktop that runs "up"

    Runtime state lives in .agora/launcher/ (pid file and logs per port; gitignored).

.EXAMPLE
    .\agora.ps1 up
    .\agora.ps1 down
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'logs', 'build', 'shortcut', 'help')]
    [string]$Command = 'help',
    [switch]$Build,
    [switch]$NoBrowser,
    [switch]$Pause,
    [int]$Port = 8000,
    [int]$StartTimeoutSec = 60,
    [int]$StopGraceSec = 10
)

$ErrorActionPreference = 'Stop'

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
    # "Ours" means: it is running the Agora API module on this port. Anything
    # else is a stranger and this launcher refuses to touch it.
    $cmd = Get-CommandLine $ProcessId
    return (($cmd -like "*$AppModule*") -and ($cmd -like "*--port*$Port*"))
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

function Open-Browser {
    if ($NoBrowser) { return }
    Start-Process $Url | Out-Null
}

function Show-LogTail([int]$Lines = 30) {
    foreach ($f in @($ErrLog, $OutLog)) {
        if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) {
            Write-Host "---- $f (last $Lines lines) ----"
            Get-Content $f -Tail $Lines
        }
    }
}

function Do-Up {
    $running = Get-RunningAgora
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
        throw "Port $Port is held by pid $($holder.Id) ($($holder.ProcessName)), which is not Agora. Stop it or use -Port <other>."
    }

    $earlier = Read-LauncherPid
    if ($null -ne $earlier) {
        throw "A backend started by this launcher (pid $earlier) is alive but not listening on $Port yet (still starting, or stuck). Wait a moment, check '.\agora.ps1 logs', or run '.\agora.ps1 down' before starting another."
    }

    $runner = Get-Runner

    if ($Build -or -not (Test-Path $OutIndex)) {
        if (-not $Build) { Write-Step "frontend\out is missing; building it once." }
        Invoke-FrontendBuild
    }

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Import-DotEnv

    Write-Step "Starting backend via $($runner.Label) on $Url ..."
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
                Write-Step "Agora is up at $Url (pid $($proc.Id), version $($health.version))."
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
        Write-Step "RUNNING at $Url (pid $($running.Listener.Id), version $($running.Health.version))."
    } elseif ($null -ne $holder) {
        if (Test-IsAgoraProcess $holder.Id) {
            Write-Fail "Port $Port is held by an Agora process (pid $($holder.Id)) that does not answer /health (starting, or stuck)."
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
                  (([string]$existing.Arguments) -like "*-File `"$scriptPath`" up*")
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
