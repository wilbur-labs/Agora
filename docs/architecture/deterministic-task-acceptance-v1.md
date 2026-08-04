# Deterministic Task acceptance v1

Status: implementation contract

## Purpose

Agora needs one executable acceptance path for the authoritative
`Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Done`
mainline that does not depend on provider availability or mutate a user's
existing Control Plane database.

`scripts/run_task_acceptance.py` runs that path in a script-owned temporary
directory. It uses the production SQLite stores, Project registry,
`ReadOnlyCliRunner`, protocol parser, routing/preflight decisions, Context and
Handoff Packs, Gate settlement, human approval, and reopen/recovery projection.

## Deterministic runtime boundary

The acceptance runtime is a local, deterministic, non-AI fixture. Each pinned
runtime command launches a real child process, but the child only converts the
sealed Context Pack into the exact contract-bound Artifact, Evidence, and
Handoff structures needed to exercise the Control Plane.

This fixture:

- never contacts Codex, Claude, Kiro, a provider API, or a local model;
- never claims provider quality, serviceability, or native-runtime acceptance;
- never substitutes one runtime for another in persisted routing authority;
- is available only through the acceptance script and an inherited acceptance
  marker; and
- writes only inside its newly created temporary directory, which is removed
  after the persisted projection has been reopened and checked.

Kiro remains a supported product runtime. Freely configurable roles and local
model adapters remain deferred until this authoritative mainline is proven;
the deterministic fixture is not their implementation.

## Acceptance checks

The command succeeds only when all of the following are true:

1. an isolated Git project and SQLite database are created;
2. all three contract Stages launch real child processes and settle passed
   Runs with sealed Handoff Packs;
3. every Stage is completed and every Stage Gate is passed from registered
   Artifact-bound Evidence;
4. the Task stops at `needs_review` with exactly the human plan-approval action;
5. explicit human approval transitions the authoritative Task to `completed`;
6. a newly opened store reproduces the completed Task, Runs, Artifacts,
   Evidence, Gates, and empty human-action set; and
7. the temporary workspace is removed.

The command emits one JSON summary. Exit code zero means the checks above
passed; it is not evidence that any native AI runtime produced a correct result.
