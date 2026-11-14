# Agora

**English** | [中文](README_zh.md) | [日本語](README_ja.md)

**Agora is a local-first workbench for people who build software with several coding
agents — Claude Code, Codex and Kiro — across several projects at once.**

Open it in the morning and it tells you, for every project, where you stopped yesterday,
what is waiting for you, and what comes next. Pick a task and you are back in the same
conversation with the same agent, on the right machine. When the work is done, the state
is written back where it belongs — in the project's own repository, not in a database
only Agora can read.

```text
Overview (every project, what waits for you)
   -> Work page (one task = one continuing conversation with an agent)
   -> Review gate (a different agent reviews before anything is committed)
   -> State written back: goal, change log (CR), decisions (ADR), progress
```

Current version: **1.10.1**. UI in English, 中文 and 日本語.

## What you get

- **Overview as the front page.** One card per project, imported from the workspace
  directories you register. Each card shows what is in progress, what is waiting for
  you, the record of its commit review gate, and which workspace it comes from. Projects
  can be paused, archived and restored; tasks are split into *todo* / *doing* and can be
  reordered by dragging.
- **One task = one continuing conversation.** The work page resumes the agent's own
  session (Claude Code, Codex or Kiro) instead of replaying history, streams the output
  live, and says plainly how long a running turn has been silent. A conversation you
  continued outside Agora is picked up again, and a restart recovers what was running.
- **A worker and a different reviewer.** Each project pins an implementing agent and a
  *different* reviewing agent — self-review is worthless. Nothing is committed until the
  review gate has actually read the change.
- **Your existing work, read in place.** Task records already kept in a workspace
  (`tasks/<name>/{task.yaml, RESUME.md, WORKFLOW.md, adr/}`) are read live and shown as
  they are — goal, change log, decisions, progress snapshot. Nothing is copied into
  Agora's database, so there is nothing to keep in sync.
- **Remote environments over SSH.** Register a host (pick it from `~/.ssh/config` if you
  like), test the connection, confirm its host key before trusting it, and run a task or
  open a terminal on it. Credentials are your SSH keys or a password kept in the OS
  keyring.
- **Built-in terminal.** An interactive terminal inside Agora, local or on a registered
  remote host, opened straight into a task.
- **Skill library.** One library of skills following the Agent Skills standard,
  installed per project for Claude, Codex and Kiro. Published skill repositories can be
  fetched, read and imported — you read a skill before you install it.
- **Usage at a glance.** Each agent's remaining limits are shown at the bottom of every
  page — Claude from its own `/usage` (Team plans included) and Kiro's credits.
- **Desktop app.** An installable Windows app with a window, a tray icon and one-click
  upgrade, wrapped around the same launcher.
- **Stable and development installs kept apart.** The stable install is exactly a
  release tag; upgrading backs the database up first and rolls back on any failure. A
  development checkout runs on its own port with its own database and keyring namespace,
  and can never touch the stable data.

### One rule that runs through everything

**Agora never states what its reads do not support.** "Could not read it", "unknown" and
"none" are three different answers and are never merged. A half-finished screen that
says "unknown" is safe to use every day; one that says "none" when it means "I could not
read it" makes you take the wrong decision.

## Requirements

- Windows 10/11 with PowerShell 5.1+ (the launcher and desktop app are Windows-only; the
  backend itself is plain Python)
- Python 3.10+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+ with `corepack` (pnpm)
- Git
- At least one agent CLI, installed and logged in: `claude`, `codex` or `kiro-cli`.
  Agents are explicit, pinned adapters — a missing or logged-out one blocks its work and
  is never silently replaced.

## Quick start

```powershell
git clone https://github.com/wilbur-labs/Agora.git
cd Agora

cd backend
uv sync --locked --extra dev
cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build
cd ..

.\agora.ps1 up          # start the backend, wait for /health, open Agora
```

The backend serves the built frontend, so day-to-day use is one process on
`http://127.0.0.1:8000`. Other launcher commands:

```powershell
.\agora.ps1 down        # stop it (never kills a non-Agora process on the port)
.\agora.ps1 status      # running? which pid? is the frontend build stale?
.\agora.ps1 logs        # last 40 lines of the backend log
.\agora.ps1 up -Build   # rebuild the frontend, then start
.\agora.ps1 shortcut    # put an "Agora" shortcut on the Desktop
.\agora.ps1 up -Dev     # this checkout as a development install on port 8001
```

### A stable install that upgrades itself

Check out a release tag in its own directory and use that one every day:

```powershell
git clone https://github.com/wilbur-labs/Agora.git Agora-stable
cd Agora-stable
git checkout v1.10.1
# install dependencies and build as above, then:
.\agora.ps1 up
.\agora.ps1 upgrade -To v1.11.0   # later: backup, checkout, build, start, verify — or roll back
.\agora.ps1 rollback -Yes          # return to the version before the last upgrade
```

### Desktop app

```powershell
cd desktop
npm install
npm run build           # produces an NSIS installer in desktop\dist
```

The app starts and upgrades Agora through `agora.ps1`, so every safety check stays in
one place.

## First steps

1. **Settings → Work sources**: register the directories that already hold your
   task records. Agora probes each one before storing it and shows what it yields,
   including what it could not read.
2. **Import projects** from those workspaces, or create a new one. Choose its worker
   and reviewer agents.
3. **Overview**: pick a task and continue it on the work page.
4. Optional: **Environments** to add a remote host, **Skills** to install skills into a
   project.

## Architecture

| Part | Path | Notes |
|---|---|---|
| Backend | `backend/agora/` | FastAPI + SQLite. Adapters for Claude Code / Codex / Kiro, sessions and live transcripts, project registry, workspace readers, SSH environments, terminal, skills |
| Frontend | `frontend/` | Next.js, built to static files the backend serves |
| Desktop | `desktop/` | Electron shell around `agora.ps1` |
| Launcher | `agora.ps1` | start / stop / status / upgrade / rollback |
| Decisions | `.agora/decisions/` | architecture decision records |

The formal control plane from Agora 1.0 (requirements, workflows, stage gates) is still
in the code base and still tested, but it is frozen and no longer in the main navigation.

## Development

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -m "not integration"
cd ..\frontend
corepack pnpm test:control-plane
corepack pnpm exec tsc --noEmit
cd ..
git diff --check
```

To exercise the whole control path without calling any AI provider or touching your
database:

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

More: [`AGENTS.md`](AGENTS.md) (architecture and conventions),
[`CONTRIBUTING.md`](CONTRIBUTING.md), [`CHANGELOG.md`](CHANGELOG.md),
[`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md) (engineering log).

## License

MIT
