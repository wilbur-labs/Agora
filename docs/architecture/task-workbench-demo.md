# Task Workbench demo

This increment exposes the provisional `agora-aidlc-foundation@0.1` planning
loop through a bounded HTTP API and a static `/tasks` workbench. It is a demo
of the Task entry point, durable orchestration state, native runtime execution,
usage visibility, restart reconciliation, retry, and explicit human approval.

It is not the final AI-DLC methodology and does not add `consult`, `decide`,
implementation dispatch, Context/Handoff Packs, or formal protocol Gate
publication. Codex, Claude, and Kiro are invoked in read-only planning modes.

## Run locally on Windows

Build the static frontend from `frontend/`:

```powershell
.\node_modules\.bin\next.cmd build
```

Then start Agora from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/tasks`.

The native `codex`, `claude`, and `kiro-cli` commands must already be installed
and authenticated. Each **Run next stage** request can remain open for several
minutes. Avoid clicking it again or restarting the server while a runtime is
active. After an interrupted server or client session, reopen the Task and use
**Reconcile interrupted run**; Agora refuses duplicate dispatch when the
recorded process is still alive or cannot be safely inspected.

## Demo path

1. Select **New guided task** and provide a project, title, description, risk,
   and token budget.
2. Run the Codex solution-design stage.
3. Inspect its semantic result and estimated usage.
4. Run the Claude correctness review and Kiro methodology review.
5. If a stage blocks, inspect its blocker, then use **Retry stage** only after
   resolving the cause.
6. After all three stages pass, record a human approval reason.

Approval marks the reviewed plan `ready_for_implementation`; this demo does not
automatically implement the plan.

## Trust boundary

The HTTP surface validates project/Task scope, identifiers, payload sizes,
budgets, state transitions, and conflicts, and moves synchronous SQLite work
off the FastAPI event loop. It inherits Agora 0.5's local trusted-user server
model and has no production authentication or tenant authorization. Do not
expose it to an untrusted network.
