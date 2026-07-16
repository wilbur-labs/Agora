# Phase 8a — Opt-in Workflow Supervision

Status: implemented.

Workflows can now opt into background dispatch with `auto_dispatch`. The default remains manual, preserving explicit operator control for existing and newly created workflows unless the composer enables automation.

Each workflow also owns a bounded `max_concurrent_runs` policy (1–32, default 4). Reconciliation counts currently running steps before claiming ready work and dispatches only into the remaining slots. This limit applies to manual dispatch and supervisor dispatch alike.

The application lifecycle starts a lightweight workflow supervisor after queued-run recovery and stops it before the execution dispatcher. At each configured interval it lists active workflows, dispatches only opt-in workflows, and records isolated scheduler errors without stopping supervision of other workflows. The supervisor recreates its asyncio lifecycle primitives on each start so application restarts and independent test event loops remain safe.

Workflow Operations exposes manual/automatic policy and concurrency in both the composer and workflow detail. Automatic activation may start root steps on the next supervisor pass; manual activation continues to require `Dispatch / reconcile`.
