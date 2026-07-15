# Phase 7b — Workflow execution dispatch

Status: backend implementation active.

Phase 7b converts dispatchable `ready` workflow steps into the existing durable execution runs. Dispatch remains explicit through `POST /api/workflows/{workflow_id}/dispatch`; one call first reconciles terminal bound runs, promotes dependencies through the Phase 7a state machine, and then queues every newly ready step that satisfies its execution preconditions.

## Dispatch preconditions

- workflow is `active`;
- step is `ready` and has no bound run;
- step references an existing task in the same project;
- task is `planned` or `running`;
- configured adapter and workspace are available.

Missing preconditions leave the step `ready` and record a bounded, redacted `workflow.dispatch_blocked` event. They do not create partial runs.

## Idempotency and crash recovery

The scheduler atomically claims a ready step with a unique workflow claim ID before queueing. The execution run persists that ID in sanitized metadata, and the workflow step then binds the generated run ID. Concurrent dispatch calls cannot claim the same step twice. Agora's current single-node scheduler also serializes dispatch, reconciliation, and recovery per workflow, so stale-claim recovery cannot race a still-running in-process queue operation.

On a later dispatch call, an unbound claim searches execution metadata for its claim ID. An existing run is rebound and a queued run is scheduled. A claim older than 60 seconds with no run is released back to `ready`. This covers crashes before and after run creation without relying on terminal keystroke injection or process-local memory.

## Reconciliation

- succeeded run → succeeded step → atomically promote newly unblocked steps;
- failed, timed-out, cancelled, or abandoned run → failed step and failed workflow;
- workflow failure cancels every still-active sibling execution run;
- dispatching an already-failed workflow performs cancellation cleanup only; sibling cancellation races are retried independently and audited without preventing cleanup of later siblings;
- completed workflow returns an empty dispatch result rather than creating more runs.

Task budgets currently supply the step timeout, capped at the execution layer's 7,200-second maximum. Cost aggregation, retry/backoff, continuous background polling, and a visual workflow page remain later increments.
