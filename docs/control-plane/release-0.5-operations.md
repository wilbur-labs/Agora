# Agora 0.5 — Operations and Acceptance

Agora 0.5 is a local-first multi-tool AI delivery control plane. Codex, Claude Code, and Kiro CLI keep their native account and rule systems; Agora supplies shared requirements, tasks, project/workspace routing, workflow orchestration, run history, human-attention handling, and audit state.

## Start locally

```powershell
cd backend
uv sync --extra dev
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
corepack pnpm install
corepack pnpm dev
```

Open `http://localhost:3000`. Configure projects and adapter commands in `config.yaml`; keep secrets in `.env`.

## Complex-task operating path

1. Capture and approve requirements before task design.
2. Create planned tasks with authoritative project, agent, budget, and acceptance state.
3. Provision or verify the selected agent workspace.
4. Compose a cross-project DAG from those tasks.
5. Keep manual dispatch for expensive or sensitive work, or explicitly enable automatic dispatch and set its concurrency cap.
6. Resolve questions and approvals in Attention Center; inspect durable output and events in Run Center.
7. Review deliverables and workflow terminal state before merging or publishing outside Agora.

Multiple projects run concurrently through separate project/workspace identities. Each workflow owns its DAG and concurrency policy; the execution dispatcher also enforces global and per-project limits.

## Acceptance commands

```powershell
cd backend
uv run pytest tests -m "not integration"

cd ..\frontend
.\node_modules\.bin\eslint.cmd .
.\node_modules\.bin\next.cmd build
```

Authenticated adapter smoke tests should be read-only and minimal:

```powershell
codex exec --ephemeral --sandbox read-only "Reply with exactly AGORA_CODEX_OK. Do not use tools."
claude -p "Reply with exactly AGORA_CLAUDE_OK"
kiro-cli chat --no-interactive --trust-tools= "Reply with exactly AGORA_KIRO_OK"
```

An account quota/session-limit response is an external availability result, not a successful adapter smoke test. Retry it after the provider reset window or with the intended account.

## Operational boundaries

- Automatic dispatch affects only active workflows that explicitly opted in.
- Claude Code and Kiro CLI currently use capture-only approval handling; Agora records the request but does not claim a bidirectional protocol that their installed CLI does not expose.
- Codex app-server supports the implemented bidirectional approval bridge; keep CLI fallback enabled for resilience.
- Agora does not merge or push repositories automatically. Treat external publication as a separate reviewed action.
- The supervisor scans up to 500 active workflows per interval; larger installations should add pagination before production scale-out.
