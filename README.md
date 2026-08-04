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

# Start the API and static Control Plane UI during development
cd ..
make dev
```

Open `http://localhost:8000/control-plane` for the authenticated Task console.
The console reads one authoritative projection and exposes only separately
reviewed, Task-scoped human actions.

For architecture and current implementation status, see:

- [`AGENTS.md`](AGENTS.md)
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md)
- [`docs/requirements/latest-transformation-requirements.md`](docs/requirements/latest-transformation-requirements.md)
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)

Agora 1.0 migration is still in progress. Do not infer feature completion from
the historical 0.5 release number or from files retained only for audited
cleanup.

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
