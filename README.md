# Agora

**English** | [Chinese](README_zh.md) | [Japanese](README_ja.md)

Agora is a local-first delivery control plane for durable AI-assisted work.
It coordinates Codex, Claude Code, and Kiro through one authoritative workflow:

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

## Important: the autonomous council is retired

The Agora 0.5 Scout / Architect / Critic / Synthesizer discussion council is
not part of the Agora 1.0 product. The default CLI, HTTP API, and web UI no
longer start AI-to-AI debate or allow model-generated consensus to advance
workflow state.

`agora task consult` is different: it is an explicit, bounded request to the
single runtime already pinned to the current Stage. Its output is only an
advisory candidate until a human explicitly adopts or rejects it.

## Core guarantees

- Agora is the only writer of cross-runtime Task, Stage, and Gate state.
- Every native runtime receives a versioned Context Pack and returns a
  versioned Handoff Pack.
- Process, transport, schema, and semantic success are recorded separately.
- Approvals bind to the repository, ref, commit, Stage, Artifact path, and
  Artifact hash.
- Review budget cannot be silently spent on implementation or consultation.
- Resume and retry are idempotent and fail closed on stale authority.

## Current entry points

```powershell
# Show the authoritative Task command family
cd backend
.\.venv\Scripts\agora.exe task --help

# Start the API and the built static Control Plane UI
$env:AGORA_CONTROL_PLANE_TOKEN = "replace-with-a-long-random-secret"
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000/control-plane` for the authenticated Task console.
The console reads one authoritative projection and exposes only separately
reviewed, Task-scoped human actions.

For locked installation, frontend build, first Task, explicit consultation,
human adopt/reject, recovery, and shutdown, follow the
[Agora 1.0 practical tutorial](docs/usage/agora-1.0-tutorial.md).

To verify the complete formal control path without calling any AI/provider or
touching the configured Agora database:

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

The command launches deterministic local child processes in a temporary Git
project, runs all formal Stages and Gates, records explicit human approval,
reopens SQLite, prints one JSON receipt, and removes the temporary workspace.
It is control-plane acceptance, not native Codex/Claude/Kiro quality evidence.

For architecture and current implementation status, see:

- [`AGENTS.md`](AGENTS.md)
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md)
- [`docs/requirements/latest-transformation-requirements.md`](docs/requirements/latest-transformation-requirements.md)
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)

Agora 1.0 is the reviewed local control-plane baseline. Native CLI
subscriptions and service availability remain external dependencies; a missing
pinned runtime blocks its Stage and is never silently replaced. Dynamic roles,
arbitrary local-model adapters, and runtime substitution are post-1.0
enhancements.

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
