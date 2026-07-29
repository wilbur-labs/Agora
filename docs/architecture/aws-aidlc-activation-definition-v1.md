# AWS AI-DLC activation definition v1

Status: complete definition baseline; not routing, dispatch, or migration authority

## Purpose

`MethodologyActivationDefinition@1.0` materializes the execution contract for
the user-confirmed AWS AI-DLC Workflows `v2.3.0` source without changing any
Task. It is bound to:

- source graph:
  `668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d`;
- upstream compiled graph:
  `9de074e882c18bcc1285a953366a7793149d05a349657a7989f0e54b2fdd1430`;
- mechanically derived activation manifest:
  `219d863f92b9162bef04f133623d020fb9c0ff48676d68521b5ddab47c2ede12`.

The sealed activation-definition hash is
`c9d9b075a5219292d94e1fa3aff2383dc1e98bb5518cd486227f85b20b45af6d`.
The generated manifest is self-contained repository data. Runtime operation
does not fetch or modify the upstream AI-DLC installation.

## Materialized Stage contracts

The definition contains all 32 source Stages in source-graph order. It
materializes:

- one Agora Stage Contract per source Stage;
- 132 upstream input bindings;
- 122 unique required or optional output Artifacts;
- source lead, support, and reviewer role profiles;
- all five `for_each=unit-of-work` expansions;
- the `code-generation` workspace requirement;
- four content-hash-bound source sensors with separate upstream and runtime
  paths;
- 232 globally unique Gate requirement templates.

The upstream scope matrix is intentionally not dependency-closed for every
required input. The definition records all 27 resulting scope/Stage/Artifact
seed requirements. A Task-scoped activation must satisfy each one from a
selected upstream Stage or an exact hash-bound seed Artifact; a missing
required input blocks activation. Agora does not silently widen the selected
scope or pretend that the Artifact exists.

Every Stage Gate requires contract-completion Evidence. Required outputs add
Artifact-registration Evidence. Source sensors add sensor-specific Evidence,
and Stages with an authored source reviewer add source-review Evidence.
Optional outputs remain registered contract outputs but do not become Gate
blockers.

The source sensor hashes are:

- `required-sections`:
  `52b9631c830eb383166173037a922ec0ccd0ef3171c1f52796864302bf2acf08`;
- `upstream-coverage`:
  `223b8a8a644bab117d8a6afbde049f721c678eacdbe61847a542ec91e1a94ed8`;
- `linter`:
  `11a082c26e181c79fb2107fdd750e5b58dfe79bc377deadaeb955e5abed67262`;
- `type-check`:
  `765688e25fd50054761f59ddf5cd68a898fe25e64b4c0a5a3c6e3e009f0adc1e`.

The export script verifies the pinned upstream compiled graph, all 46
source-graph artifacts, Stage order, `for_each` values, sensor declarations,
output classification, and reviewer bounds before regenerating the checked-in
manifest.

## Runtime and independence policy

The definition assigns runtime families by Agora responsibility:

```text
production execution       -> Codex
independent correctness    -> Claude
methodology stewardship    -> Kiro
final completion approval  -> human
```

These runtime families must remain pairwise distinct and Task-pinned. Native
AWS agent identities are source profiles inside the production execution
contract; they are not Agora routing authority and cannot substitute for
independent Claude or Kiro Evidence.

## Budget, quality, and rework policy

The definition does not invent static per-Stage Token or cost limits. An
activating Task must supply a bounded Task envelope and Run reservations,
retain the explicit unbounded-native-usage acknowledgement, and settle usage
as exact observation or conservative reservation.

Process, transport, schema, and semantic result remain independent. One
format-only repair is allowed. Eleven upstream Stages carry an authored source
reviewer bound of two iterations; that bound is preserved exactly.

AWS `v2.3.0` does not author structured cross-Stage rework edges. Agora
therefore forbids automatic cross-Stage rework. Exhausted source review or a
failed Gate blocks the Stage and escalates. Keep/Modify/Redo requires an
explicit Task decision.

## Approval and migration boundary

Migration and final completion require explicit human approval. The approval
policy requires repository, ref, commit, Task, Stage, Artifact path/hash,
source-graph hash, and activation-definition hash bindings.

The definition records:

```text
routing_authority = false
dispatch_authority = false
migration_authority = false
```

Existing Tasks retain their pinned methodology. In-place methodology mutation
and automatic rerouting are forbidden, and no activation command is exposed
by this increment.

The next safe backend slice is a sealed, Task-scoped methodology migration
preview/decision that validates the exact Task, repository revision, source
graph, activation definition, selected scope, runtime pins, budget, and human
Gate before any later transactional migration implementation. HTTP and UI
remain deferred.
