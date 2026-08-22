# Agora

**English** | [Chinese](README_zh.md) | [Japanese](README_ja.md)

**Agora is a local-first console for supervising several coding agents at once.**
You give a project a folder, a *worker* agent, and a *different reviewer* agent.
The worker builds, the reviewer independently cross-reviews its work, and nothing
advances until you approve. Agora is the single authority that records every
task, run, and decision across Codex, Claude Code, and Kiro.

```text
Project (folder + worker agent + reviewer agent)
   -> Task
   -> Worker run (isolated git worktree)
   -> Automatic cross-review by a different agent
   -> Your approval
   -> Verified
```

The point is triage: one person watching many agents, seeing at a glance what
needs a reply, what needs approval, and what is blocked — instead of babysitting
each agent in its own terminal.

## What you get

- **Attention-first Portfolio.** Tasks are grouped into *Needs you* / *In
  progress* / *Done*, colored by the one signal that matters — a question to
  answer, an approval to give, or a blocker — so the work that needs a human
  surfaces first.
- **Per-project worker + reviewer.** Each project pins a default implementer and
  a *different* review agent (e.g. Codex builds, Claude reviews). Self-review is
  worthless, so the reviewer is always another agent.
- **Automatic cross-review.** When a worker run succeeds, Agora dispatches an
  independent review run by the project's reviewer agent, feeds it the worker's
  deliverables, and raises the result as an approval for you. Approve and the
  task advances to *verified*; the review has real teeth — it reports concrete
  findings, not a rubber stamp.
- **Isolated deliverables.** Every agent workspace is its own git repository, so
  a run's output is exactly the files it produced — cleanly shown in the UI and
  reviewed without noise from the surrounding repo.
- **In-app requirements interview.** The agent asks clarifying questions on the
  page and your answers become the spec — clarification is a product step, not
  something done in a chat window on the side.
- **Settings that tell the truth.** The Settings page probes each runtime's
  install, version, and login status (without spending tokens), reports Claude
  token usage per agent, and lets you set the project root and execution limits.
- **A durable control plane underneath.** Agora is the only writer of
  cross-agent task and gate state; approvals bind to repo, ref, commit, and
  artifact; resume and retry are idempotent and fail closed on stale authority.
  Pinned runtimes are never silently substituted — a missing one blocks its work
  instead.

> The Agora 0.5 autonomous Scout / Architect / Critic / Synthesizer debate
> council is retired and is not part of this product; model output never
> advances state on its own.

## Quick start (Windows)

```powershell
cd backend
uv sync --locked --extra dev

cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build

cd ..\backend
$env:AGORA_CONTROL_PLANE_TOKEN = "replace-with-a-long-random-secret"
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/portfolio` for the attention-first board, or
`http://127.0.0.1:8000/control-plane` for the authenticated task console. Use
project `agora` and the same bearer token. From there you can create a project
(it gets its own folder and isolated agent workspaces), assign its worker and
reviewer agents, and run tasks.

To verify the whole formal control path without calling any AI provider or
touching the configured database:

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

It launches deterministic local child processes in a temporary Git project, runs
every stage and gate, records an explicit human approval, reopens SQLite, prints
one JSON receipt, and removes the workspace. That is control-plane acceptance,
not evidence of native Codex/Claude/Kiro output quality.

## Runtimes

Codex, Claude Code, and Kiro are explicit, pinned CLI adapters. Their
subscriptions and availability are external dependencies: a missing or
logged-out runtime blocks its work and is never silently replaced. Detection and
login status are surfaced on the Settings page.

## Learn more

- [`AGENTS.md`](AGENTS.md) — architecture and conventions
- [Agora practical tutorial](docs/usage/agora-1.0-tutorial.md) — install, first task, approval, recovery, shutdown
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md) — the control-plane protocol
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md) — implementation status

## Development

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -m "not integration"
cd ..
.\backend\.venv\Scripts\python.exe scripts\export_protocol_schemas.py --check
git diff --check
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines.

## License

MIT
