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

## Ubuntu Docker acceptance

Use Ubuntu 22.04 or 24.04 with Docker Engine 24+ and Docker Compose v2. Run this acceptance from a clean clone of the intended release commit or tag.

### 1. Verify prerequisites and prepare durable directories

```bash
docker version
docker compose version
git rev-parse --short HEAD

cp .env.example .env
# Add only the provider credentials needed for authenticated functional tests.
mkdir -p data .agora agora-workspace skills/public skills/learned skills/custom
docker compose config --quiet
```

The infrastructure smoke below does not call a paid model, so blank provider values are acceptable. Do not commit `.env`.

### 2. Build from scratch and start the API

```bash
docker compose down --remove-orphans
docker compose build --no-cache agora-api
docker compose up -d agora-api

timeout 120 sh -c 'until curl -fsS http://127.0.0.1:8000/health >/dev/null; do sleep 2; done'
docker compose ps
docker compose logs --no-color --tail=200 agora-api
```

Pass criteria: the image builds without a missing-path error, `agora-api` is `running (healthy)`, and the recent logs contain no traceback or restart loop.

### 3. Verify health, release version, UI, and control-plane API

```bash
curl -fsS http://127.0.0.1:8000/health | tee /tmp/agora-health.json
grep -q '"status":"ok"' /tmp/agora-health.json
grep -q '"version":"0.5.0"' /tmp/agora-health.json

curl -fsS http://127.0.0.1:8000/openapi.json | grep -q '"version":"0.5.0"'
curl -fsS http://127.0.0.1:8000/ | grep -qi '<!doctype html'
curl -fsS http://127.0.0.1:8000/api/execution-adapters | tee /tmp/agora-adapters.json
curl -fsS 'http://127.0.0.1:8000/api/tasks?limit=1' >/dev/null
curl -fsS 'http://127.0.0.1:8000/api/workflows?limit=1' >/dev/null
```

Pass criteria: every command exits with status 0, health and OpenAPI report `0.5.0`, the root serves the exported web UI, and the control-plane read endpoints return JSON.

### 4. Verify state survives container recreation

```bash
TASK_ID="$(curl -fsS -X POST http://127.0.0.1:8000/api/tasks \
    -H 'Content-Type: application/json' \
    -d '{"project_id":"agora","title":"Ubuntu Docker persistence acceptance"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')"

test -n "$TASK_ID"
test -s data/agora.db
test -f .agora/projects.yaml

docker compose down
docker compose up -d agora-api
timeout 120 sh -c 'until curl -fsS http://127.0.0.1:8000/health >/dev/null; do sleep 2; done'

curl -fsS "http://127.0.0.1:8000/api/tasks/$TASK_ID" \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["title"] == "Ubuntu Docker persistence acceptance"'
```

Pass criteria: the task remains readable after `docker compose down` recreates the container, proving the SQLite `data/` and project `.agora/` bind mounts are effective.

### 5. Optional authenticated adapter acceptance

The stock image validates the API, UI, persistence, workflow scheduler, and Docker sandbox boundary. It does not install host-native `codex`, `claude`, or `kiro-cli` binaries. Test those adapters on the Ubuntu host, or build an approved derived image that installs the required CLI and mounts its authentication deliberately. Do not report adapter acceptance merely because `/api/execution-adapters` lists configured commands.

### 6. Capture evidence and clean up

Save the following with the release evidence: Ubuntu version, Docker and Compose versions, commit/tag, build result, `docker compose ps`, health JSON, relevant logs, persistence task id, and any authenticated adapter result.

```bash
docker compose logs --no-color agora-api > agora-0.5-docker.log
docker compose down
```

Use `docker compose down -v` only if named-volume data may be discarded. The bind-mounted `data/`, `.agora/`, `skills/`, and `agora-workspace/` directories are intentionally retained unless removed manually.

## Operational boundaries

- Automatic dispatch affects only active workflows that explicitly opted in.
- Claude Code and Kiro CLI currently use capture-only approval handling; Agora records the request but does not claim a bidirectional protocol that their installed CLI does not expose.
- Codex app-server supports the implemented bidirectional approval bridge; keep CLI fallback enabled for resilience.
- The default Compose file mounts `/var/run/docker.sock` for Docker sandbox execution. Treat that container as host-privileged and run it only on a dedicated, trusted machine; remove the mount when sandbox execution is not required.
- Agora does not merge or push repositories automatically. Treat external publication as a separate reviewed action.
- The supervisor scans up to 500 active workflows per interval; larger installations should add pagination before production scale-out.
