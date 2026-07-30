# AWS AI-DLC methodology migration preview v1

Status: read-only eligibility preview; not a migration authorization or writer

## Purpose

`MethodologyMigrationPreviewRequest@1.0` and
`MethodologyMigrationPreviewDecision@1.0` describe whether one exact current
Task is eligible to be considered for a later AWS AI-DLC `v2.3.0` migration
transaction. The only supported strategy is `successor_task`. The preview
never changes the current Task's pinned methodology, Plan, grouped Stage
inventory, Stage/Gate state, or compatibility projections.

The CLI surface is:

```powershell
agora task migration-preview TASK_ID --request PATH
```

It prints a hash-sealed decision. Exit code `0` means every preview constraint
was satisfied. Exit code `2` means either a hash-sealed blocked decision was
printed to stdout or the preview could not be produced and an `error:` message
was printed to stdout. Callers must parse the output rather than treating the
exit code as semantic authorization. Neither exit code grants migration,
routing, dispatch, or persistence authority.

## Exact input bindings

The hash-sealed request binds:

- Project, Task, Task manifest version, authoritative Control Task version and
  status, Plan identity and version;
- the current methodology identity, version, and hash;
- a clean repository identity, ref, and commit;
- the target activation identity, methodology identity/version, source-graph
  hash, and activation-definition hash;
- one of the nine pinned source scopes and the exact hash-bound Task seed
  files required to close that scope's upstream input gaps;
- pairwise-distinct Codex, Claude, and Kiro responsibility pins, each bound to
  the current runtime command hash and the complete runtime-registry hash;
- the Task Token/cost envelope, explicit unit-of-work count, every selected
  source Stage's per-instance allocation and maximum Run reservation, plus
  protected non-zero reservations for all three runtime families;
- an optional `MethodologyMigrationGateAssertion@1.0`.

For a source Stage with `for_each_artifact=unit-of-work`, the proposed instance
count must exactly equal the request's explicit unit-of-work count. Every
other selected source Stage must have exactly one instance. The proposal must
contain exactly the selected scope's Stage keys; Agora does not invent a
default Stage budget or silently omit a selected Stage. Stage allocations and
protected runtime reservations must fit the current Task envelope. If the
Task has no cost envelope, all proposed cost values must be absent.

Seed and migration proposal files use canonical repository-relative paths.
Observation resolves each file inside the registered project root, rejects an
unreadable or changing file, and hashes at most 16 MB per file and 64 MB in
total.

## Deterministic preview constraints

The decision always reports these checks in order:

1. `task_binding`
2. `current_methodology_binding`
3. `repository_binding`
4. `target_source_binding`
5. `scope_selection`
6. `scope_seed_artifacts`
7. `runtime_pins`
8. `budget`
9. `human_gate`
10. `task_quiescence`

Task, Plan, authoritative Control Task, sealed grouped inventory, and
quiescence counters are read through one rollback-only database snapshot.
Quiescence requires no active orchestration Run, no active consultation, and
no unsettled formal protocol Run. A failed, cancelled, or actively executing
authoritative Task blocks the preview.

`blockers` is exactly the ordered list of failed checks, and `eligible` is true
only when that list is empty. The decision also records the observed Task,
Control Task, Plan, repository, and quiescence facts so later code does not
need to infer what was evaluated.

## Human Gate and authority boundary

The human Gate assertion binds the successor strategy, current Task and
methodology optimistic-lock facts, repository and migration proposal
Artifact path/hash, target source and activation hashes, selected scope,
runtime registry, Stage/runtime budget proposal, and seed Artifact set.

In this increment it is only hash-sealed request input. The preview does not
authenticate the asserted human identity, persist an authoritative Gate
record, or consume approval. Therefore an `eligible=true` decision is
advisory evidence, not proof that a migration is authorized.

Every decision fixes:

```text
preview_only = true
state_mutated = false
plan_mutated = false
inventory_mutated = false
runtime_spawned = false
migration_executed = false
routing_authority = false
dispatch_authority = false
migration_authority = false
```

The activation definition remains non-authoritative. Native provider
availability, advisory output, and process exit code cannot turn the preview
into a writer.

## Transactional successor boundary

The separately reviewed writer in
`aws-aidlc-methodology-migration-activation-v1.md` may consume the same request,
but it never treats this preview decision as authorization. It authenticates
and persists the human migration Gate and, inside one writer transaction,
rechecks every optimistic-lock binding, methodology hash, repository revision,
source/activation hash, scope seed, runtime pin, budget, and quiescence fact.
Only a fresh eligible recheck may atomically create the successor Task, Plan,
and grouped Stage inventory. The current Task is not mutated in place.

HTTP, UI, route activation, runtime dispatch, dynamic provider substitution,
and native AI-DLC file installation remain deferred.
