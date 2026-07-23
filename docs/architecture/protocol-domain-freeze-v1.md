# Agora Protocol & Domain Freeze v1

Status: implementation baseline
Consensus date: 2026-07-16
Protocol major version: 1

## 1. Product boundary

Agora is a local-first delivery control plane for Git projects and multiple AI
CLI runtimes. Its only product mainline is:

```text
Project -> Task -> Stage -> Run
        -> Artifact/Evidence
        -> Gate
        -> Handoff/Done
```

Codex, Claude Code, and Kiro CLI retain their native sessions, rules, skills,
memory, and methodology. They submit events, artifacts, evidence, native-state
assertions, and completion proposals. Only Agora changes cross-runtime Task,
Stage, and Gate state.

This freeze implements the protocol foundation. It does not yet migrate the
0.5 database, replace the existing UI, or claim that the 1.0 control plane is
complete.

## 2. Normative artifacts

The checked-in schemas under `docs/architecture/schemas/` are generated from
the Pydantic models in `backend/agora/protocol/`:

- `context-pack.schema.json`
- `handoff-pack.schema.json`
- `native-state-snapshot.schema.json`
- `artifact.schema.json`
- `evidence.schema.json`
- `approval.schema.json`
- `run-protocol-state.schema.json`
- `gate-requirement.schema.json`
- `runner-isolation-contract.schema.json`
- `stage-inventory.schema.json`
- `provider-usage-observation.schema.json`
- `native-runtime-capability-observation.schema.json`

`scripts/export_protocol_schemas.py --check` fails when a checked-in schema
does not match its executable model.

Protocol objects reject unknown fields. Version `1.x` permits additive optional
minor-version fields only. An unsupported major version blocks the Stage;
adapters must not guess unknown required fields.

## 3. Stable identities and hashes

Protocol IDs are stable, bounded identifiers. SHA-256 values are lowercase
hexadecimal strings.

Canonical hashes use UTF-8 JSON with keys sorted by Unicode code-point order
and compact separators. Every consumer must use that exact ordering. The
`content_sha256` field is excluded from its own calculation.

Artifact identity is versioned and traceable to either:

- managed UTF-8 content held by Agora; or
- a referenced repository/ref/commit/path with a content hash.

Native snapshot identity is:

```text
project_id
+ repository_id
+ canonical_ref
+ commit_sha
+ native_state_sha256
+ reconciliation_rule_version
+ methodology
```

Snapshot generation contains no wall-clock field. Identical normalized inputs
must produce byte-identical output.

Context Packs are different: `generated_at` is part of the sealed pack content,
so two pack instances generated at different times intentionally have different
content hashes.

## 4. Domain state machines

### Task

```text
backlog | ready | active | blocked | needs_review |
completed | failed | cancelled
```

Completed work may reopen to `active` only through an explicit invalidation or
reconciliation event. Runtime adapters cannot invoke Task transitions directly.
Persisted Task lifecycle is derived from the complete hash-sealed Stage
inventory plus authoritative Stage, Gate, Attention, invalidation, and
reconciliation state. Passing every Stage Gate enters `needs_review`; explicit
human approval completes the Task.

### Stage

```text
pending | ready | running | blocked | needs_review |
reconciliation_required | completed | failed | cancelled
```

A completed Stage may reopen to `ready` when an approval or required artifact
becomes stale.

For a Task with a sealed grouped inventory, Agora routes the first incomplete
Stage in inventory order and only that route may start a formal Run. Successful
formal settlement activates the next route atomically; compatibility Plan state
does not select the Stage or runtime.

Before dispatching that pinned route, Agora records a hash-sealed routing-policy
decision that verifies the Stage/runtime capability binding, Task-risk reviewer
coverage, reviewer independence, and protected budget for every unfinished
required reviewer Stage. The policy is re-derived in the Run-claim transaction;
it cannot substitute a runtime or alter the sealed methodology graph. Budget
pressure must block before process spawn rather than remove a required review.

When that protected-budget check is the only policy blocker, a versioned Task
budget amendment may increase the total Task/Plan envelope without changing
Stage allocations, reviewer requirements, or historical usage. The amendment
records sealed policy snapshots before and after the increase and commits only
when the resulting policy passes. Every subsequent Run claim still derives a
new per-Run policy inside its own transaction; the amendment receipt is audit
evidence, not dispatch authority.

Native provider usage and native runtime capability observations are separate
read-only, hash-sealed contracts. Usage observations bind measured Run results
without rewriting historical ledger entries. Capability observations bind
local installation/version probes and declared model/capability provenance but
carry `routing_authority: false`; they cannot select a runtime/model or alter
the sealed route.

### Gate

```text
pending | evaluating | passed | blocked | stale
```

`passed -> stale` is mandatory when a bound approval or artifact changes.

### Run protocol dimensions

Every Run records four independent dimensions:

```text
process_status
transport_status
schema_status
semantic_stage_result
```

Semantic success requires an exited process, completed transport, and a valid
or repaired schema. Exit code zero alone is never sufficient.

Schema repair is limited to one format-only attempt. A second invalid response
is `protocol_failed`, blocks the Stage, and creates Attention.

## 5. Context and handoff contracts

A Context Pack is immutable input for one Run. It includes:

- task and stage identity;
- Stage Contract and required outputs;
- applicable Policies;
- verified M2 Task Memory;
- approved/pinned M3 Project Knowledge;
- minimal user preferences;
- versioned input Artifact references;
- forbidden constraints and budget.

A Handoff Pack is immutable output from one Run. It includes:

- semantic Stage result;
- output Artifact versions;
- Evidence;
- unresolved questions;
- optional NativeStateSnapshot;
- M2 candidates;
- blocker requirement IDs;
- an Agent-suggested next action.

The authoritative `next_safe_action` is derived by the Gate evaluator, not
copied blindly from the Agent suggestion.

## 6. Evidence and Gate evaluation

Evidence statuses are:

```text
passed | failed_product | failed_external | missing | stale
```

Gate requirements have a stable `requirement_id`, severity, priority, and
failure action, plus repository/ref/commit scope. Evidence carries the same
scope. Lower numeric priority is more urgent.

Evaluation is deterministic and fail-closed:

1. no current Evidence becomes `missing`;
2. one `passed` status satisfies the requirement;
3. conflicting current statuses fail closed;
4. blocker requirements prevent Gate passage;
5. warning requirements are reported but do not block;
6. `next_safe_action` comes from the highest-priority blocker, with
   `requirement_id` as the deterministic tie-breaker.

The evaluator ignores Evidence from another repository, ref, or commit. Callers
should still provide only the active Evidence set for the evaluated Artifact
versions. Historical Evidence remains in the ledger but is not mixed into the
current Gate input.

## 7. Approval invalidation

Approval binds:

```text
repository + ref + commit + stage + artifact path + artifact hash
```

When a bound requirements, design, contract, or dependent Artifact hash
changes, its bound commit changes, or the Artifact disappears from the complete
current inventory:

1. matching Approval becomes `stale`;
2. the approved Stage and all configured downstream Stages reopen;
3. a deterministic impact-analysis Attention is required;
4. the prior Gate becomes `stale`;
5. retries and branch switches cannot reactivate the old Approval.

Approvals from another ref are not portable and cannot satisfy the active ref.

## 8. Native reconciliation

Native state is a declaration, not a completion fact. Reconciliation is a
deterministic, read-only, idempotent function over:

```text
declared native state
+ audit and version-bound approval
+ required Artifact set and hashes
+ Git ref, commit, and lineage
```

Blocking conflict classes:

- `state_stale`
- `audit_stale`
- `internal_contradiction`
- `required_evidence_missing`
- `approval_missing_or_stale`

Default warning classes:

- `branch_divergence`
- `policy_reassessment_required`
- `location_stale`

Other refs may create divergence Attention but cannot overwrite the active ref
or contribute Approval.

## 9. M2 publication rules

```text
Run running
  -> M1 ledger + M2 candidate only

failed | cancelled | protocol_failed
  -> preserve latest verified M2; append attempt/blocker

succeeded + Gate blocked
  -> publish unverified M2 draft; preserve verified facts

Gate passed
  -> atomically publish a new verified M2 version and Handoff
```

A retry starts from the latest verified M2, current Artifact versions, and
latest blockers. It does not inherit the failed Run's complete transcript.

## 10. Windows Embedded Runner contract

The 1.0 contract is explicitly `platform: windows`. Each Run receives isolated
writable HOME, temp, cache, and config directories under its run root. The
workspace must be inside an explicit allowed workspace root. Credentials are
injected only by opaque references and never serialized into packs, logs, or
artifacts. POSIX and remote runner contracts remain a 1.1+ extension rather than
being silently accepted by the Windows validator.

Global initialization or credential-helper operations that cannot be isolated
must be listed and serialized. Cleanup failure writes a recovery marker and
creates Attention; it must not silently discard the workspace.

## 11. Acceptance fixtures

The regression fixtures under `backend/tests/fixtures/protocol/` preserve two
real findings:

- `workflow-polish`: process launch can succeed while external authentication
  Evidence fails, so the Gate remains blocked.
- `deal_analysis`: declared “Build and Test Complete” conflicts with stale
  state/audit, missing required artifacts, and missing final approval; verified
  state is `reconciliation_required`.

These fixtures are sanitized protocol inputs, not copies of external project
content.

## 12. Freeze exit criteria

The first implementation stage is complete only when:

- all checked-in schemas match executable models;
- state-machine, Gate, invalidation, M2, hash, and Runner isolation tests pass;
- workflow-polish and deal_analysis fixtures pass deterministically;
- the full relevant backend suite passes;
- independent Claude Code review approves the diff;
- progress is updated with the reviewed commit and next safe action.
