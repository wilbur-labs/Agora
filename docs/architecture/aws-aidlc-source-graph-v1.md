# AWS AI-DLC source graph v1

Status: source-bound definition baseline; not dispatch authority

## Authority and provenance

On 2026-07-28 the user identified the AWS AI-DLC Method Definition and the
public `awslabs/aidlc-workflows` repository as the authoritative AI-DLC source
for Agora. This baseline pins the executable source material to:

- repository: `https://github.com/awslabs/aidlc-workflows`;
- release: `v2.3.0`;
- peeled commit: `29a31f7899731b53f2b8d7f76cd223f9a8a25859`;
- upstream license identifier: `Apache-2.0`;
- Method Definition Paper:
  `https://prod.d13rzhkk8cj2z0.amplifyapp.com/aidlc.pdf`, SHA-256
  `6fdd881f6a56a4d1bed3605ca9c167011f92ef6430588679311430ce95fc692f`;
- method specification:
  `assets/AI-DLC-Workflows-2.0-Specification.pdf`;
- canonical Stage definition contract:
  `core/aidlc-common/protocols/stage-definition.md`;
- recovery/rework prose:
  `core/aidlc-common/protocols/stage-protocol-recovery.md`.

`MethodologySourceGraph@1.0` binds the external Method Definition Paper by URL
and SHA-256. It also records SHA-256 values for the repository specification,
Stage format, recovery protocol, compiled Codex graph, scope grid, all 32
canonical Stage files, and all 9 scope files. Agora does not vendor, rewrite,
or silently install the upstream native files. The checked-in hashes and
derived graph are self-contained provenance; runtime operation does not fetch
the paper or upstream repository.

## Frozen source graph

The source graph contains five ordered phases:

1. initialization;
2. ideation;
3. inception;
4. construction;
5. operation.

It records all 32 upstream Stages with their stable source number, key, title,
phase, `ALWAYS`/`CONDITIONAL` classification, execution mode, dependency
edges, scope membership, and source path.

The graph also records all 9 upstream scope profiles:

- `enterprise`;
- `feature`;
- `mvp`;
- `poc`;
- `bugfix`;
- `refactor`;
- `infra`;
- `security-patch`;
- `workshop`.

Scope membership is the first frozen branch matrix. `enterprise` and `feature`
select all 32 Stages, while narrower scopes select only their declared
subgraphs. Five Construction Stages carry `for_each_artifact=unit-of-work`, so
one source node can expand into multiple runtime Stage instances only after a
future reviewed activation contract defines that expansion.

## Fail-closed validation

The protocol model rejects:

- duplicate phases, Stage keys, source numbers, scopes, or source paths;
- missing or duplicate required source-role artifacts, including the external
  Method Definition Paper;
- unknown phases, dependencies, or scopes;
- self, forward, or cyclic dependency edges;
- Stage or scope nodes without an exact hash-pinned upstream source file;
- drift between the source manifest and the graph;
- unknown protocol fields or an invalid graph content hash.

The complete graph is canonically sealed. The pinned
`MethodologySourceGraph@1.0` hash is
`668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d`.

## Rework and execution boundary

AWS AI-DLC Workflows `v2.3.0` describes Keep/Modify/Redo, major-change impact
analysis, scope-change returns, architecture-change returns, self-correction,
halting, and human escalation in specification/protocol prose. Its Stage
definition contract explicitly reserves structured `on_failure`, `timeout`,
and `retry` fields for future versions; those fields are not authored in the
32 Stage definitions.

Agora therefore records:

```text
structured_rework_edges = false
routing_authority = false
dispatch_authority = false
```

It does not invent per-Stage rework edges, retry counts, Token limits, Gate
requirements, or runtime assignments. The source graph cannot initialize a
Task, replace `agora-aidlc-foundation@0.1`, select a runtime, create a Stage,
or affect Control Plane routing.

The separate `MethodologyActivationDefinition@1.0` now materializes and
hash-binds:

- Stage Contracts and required outputs;
- Agora runtime-role and independence mappings;
- Artifact, Evidence, Approval, and Gate requirements;
- budget/quality policy;
- bounded rework limits and escalation behavior;
- Task methodology activation/migration semantics.

That definition has no routing, dispatch, or migration authority. It preserves
the existing provisional method and current Tasks. A sealed, read-only
successor-Task migration preview now checks the exact current state, selected
scope inputs, runtime pins, and explicit Stage/runtime budget proposal. Its
human Gate assertion is non-authoritative preview input. The separate reviewed
migration writer authenticates and persists that Gate, rechecks every binding
inside one transaction, and atomically creates a distinct successor
Task/Plan/inventory while preserving the predecessor. The successor remains
non-dispatching until a later reviewed route-activation contract exists.
