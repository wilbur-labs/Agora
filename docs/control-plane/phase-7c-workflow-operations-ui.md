# Phase 7c — Workflow Operations UI

Status: implementation active.

The Delivery Control Plane now includes `/workflows`, a read-oriented operations surface for cross-project DAGs. It lists durable workflow summaries, polls active details without mutating state, lays steps out by dependency depth, and exposes project, adapter, task, run, state, and dispatch-blocker context.

Operators explicitly choose `Activate` or `Dispatch / reconcile`. Read polling never queues work. A failed workflow exposes `Cleanup`, which invokes the Phase 7b cleanup-only dispatch path. Dispatch results report newly queued run IDs and retryable blockers inline.

Step cards link to their requirement task and bound execution run. The Run Center accepts a `run` query parameter so workflow-to-run navigation opens the correct detail directly.

This increment deliberately excludes the workflow composer, background mutation scheduler, graphical edge routing, and budget dashboard. Those remain separate reviewable increments because DAG authoring needs its own accessible dependency editor and client-side cycle validation.
