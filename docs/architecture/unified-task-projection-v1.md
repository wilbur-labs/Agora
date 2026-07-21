# Unified Task projection v1

Status: reviewed implementation baseline.

This increment added the first read-only Task status/progress/result projection
over the formal Control Plane and the temporary 0.5 orchestration compatibility
ledger. It does not add a state writer, map the legacy Task enum into the frozen
Task state machine, expose a new HTTP route, or start Task Workbench UI work.

## Entry point

The CLI-first projection is explicit while legacy Tasks remain supported:

```text
agora task status TASK_ID --protocol-v1
agora task status TASK_ID --protocol-v1 --json
agora task status TASK_ID --protocol-v1 --json --limit 100 --offset 0
```

`--limit` and `--offset` page historical collections together. Current Stage,
Gate, and Attention state remains present on every page. The existing status
command without `--protocol-v1` retains its compatibility response.

## Authority and consistency

The projection opens one SQLite connection, begins one read transaction, reads
all contributing tables through that snapshot, and always rolls the transaction
back. It never expires Attention, reconciles a native runtime, advances a Stage,
evaluates a Gate, or appends an event.

The sources are intentionally explicit:

- `tasks` supplies the Agora-owned legacy Task manifest and its current state;
- `control_stages`, `control_gates`, and `protocol_runs` supply authoritative
  formal Stage, Gate, and Run protocol dimensions;
- protocol Artifact, Evidence, Approval, and Attention ledgers supply formal
  outputs, quality reasons, approvals, and unresolved human work;
- orchestration Plan, Stage, Run, decision, and usage ledgers supply dispatch,
  runtime, attempt, reservation, settlement, and temporary compatibility data;
- Task and Control Plane events are merged into one ordered audit history.

The initial `1.0` projection labeled Task state as `task_manifest` because no
frozen state persistence existed. The frozen Task-state increment supersedes
that field with the independent `control_tasks` projection and moves the unified
JSON shape to schema version `2.0`. Missing frozen state is explicitly
unavailable; it is never inferred from the compatibility manifest. The only
authoritative `next_safe_action` remains the Control Plane Gate-derived value.
The old orchestration hint remains visible under `compatibility_next_action` and
is explicitly non-authoritative.

Version `2.0` also exposes
`task_state_lifecycle=stage_derivation_deferred`. Frozen state transitions are
explicit in this increment; projected Stage completion does not silently derive
Task completion until the complete grouped Stage inventory exists.

Formal progress counts only Control Plane Stages in `completed` state. A passed
process, compatibility Run, or Gate cannot increase the completed count by
itself. Operational Stages not yet configured in the Control Plane remain
visible as `unconfigured` and count as remaining work.

## Run and recovery projection

Each Run summary keeps the four frozen protocol dimensions separate from the
operational state and usage fields. It also reports one derived wait state:

- `operational_runtime_pending` for a running legacy dispatch;
- `protocol_start_pending` after a formal operational reservation but before a
  formal Run record exists;
- `runtime_or_settlement_pending` while a formal Run has no settlement;
- `compatibility_projection_pending` when formal settlement committed before
  the temporary orchestration projection;
- `settled` for a terminal operational or formal Run.

Elapsed time is derived at the projection snapshot from persisted, timezone-
aware timestamps. The projection does not inspect a live PID; `task resume`
continues to own process reconciliation and duplicate-dispatch prevention.

## Budget truthfulness

Budget totals are computed with bounded SQL aggregates rather than loading the
complete usage history. Active reservations and settlements remain separate.
Any unavailable Token settlement makes aggregate Token use and remaining
capacity unavailable. Cost is exact zero only before any settlement exists; if
any settled Run lacks cost, aggregate cost and remaining cost become
unavailable rather than zero. Provider-specific exact usage remains deferred.

## Bounds and payload shape

- history page limit: 1 to 200, offset: 0 to 1,000,000;
- current Stage/Gate inventory: at most 200, otherwise fail closed;
- Attention: the first 200 items with open items ordered first and a true total;
- Artifact bodies are omitted; only versioned Artifact references and producer
  metadata are returned;
- Run stdout and prompts are omitted; failure, finding, and summary strings are
  projection-bounded;
- audit payloads above 16 KiB are replaced by a SHA-256/byte-count summary;
- every collection reports a true total and its projection window.

## Deferred boundaries

The future authenticated HTTP projection should reuse this read model rather
than construct another status interpretation. Frozen Task-state persistence is
now supplied by `control_tasks`; grouped Stage-driven lifecycle wiring, dynamic
routing, exact provider usage, the authoritative full AI-DLC graph, and Task
Workbench UI remain separate reviewed increments.
