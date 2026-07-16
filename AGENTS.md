# Agora Development Contract

This repository is being migrated from the 0.5 delivery control plane to the
consensus Agora architecture. The implementation source of truth is:

1. `docs/architecture/protocol-domain-freeze-v1.md`
2. checked-in JSON Schemas under `docs/architecture/schemas/`
3. executable protocol models and tests under `backend/agora/protocol/`
4. `.agora/development/PROGRESS.md`

Research notes outside this repository are provenance, not runtime
dependencies.

## Resume procedure

At the start of every development session:

1. Read this file and `.agora/development/PROGRESS.md`.
2. Run `git status --short --branch` and preserve all pre-existing user
   changes.
3. Verify the recorded commit and next safe action.
4. Reconcile any interrupted run or dirty worktree before new implementation.

Do not infer progress from a previous chat transcript alone.

## Implementation rules

- Keep the product mainline:
  `Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done`.
- Agora is the only writer of cross-runtime Task, Stage, and Gate state.
- Native runtime state is an assertion until deterministic reconciliation
  verifies it.
- Exit code zero is not semantic success. Preserve process, transport, schema,
  and semantic result as separate dimensions.
- State, formal artifacts, policy, approvals, evidence, and memory are separate
  concepts. Memory never substitutes for authoritative state or evidence.
- Approval is bound to repository, ref, commit, stage, artifact path, and
  artifact hash. Changed dependencies make prior approval stale.
- A runtime receives a versioned Context Pack and returns a versioned Handoff
  Pack. Do not use a full prior transcript as the handoff contract.
- Protocol parsers fail closed. One format-only repair is allowed; repair must
  not invent evidence, remove blockers, or alter semantic results.
- Never silently modify, move, or rename native Codex, Claude, Kiro, or AI-DLC
  files.
- Keep changes small enough to test and review independently.

## Persistence and review gate

After every meaningful increment:

1. Update `.agora/development/PROGRESS.md` with tests, review state, blockers,
   and the next safe action.
2. Run relevant automated tests and schema consistency checks.
3. Obtain a Kiro CLI review for protocol, AI-DLC, reconciliation, and
   methodology boundaries.
4. Obtain an independent Claude Code review for implementation correctness,
   safety, and regression coverage.
5. Fix actionable findings from both reviewers and rerun tests.
6. Commit only after both review gates approve.

No implementation commit may be created while the review gate is incomplete.
User-owned unrelated changes must not be staged with Agora implementation
commits.

## Verification commands

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -m "not integration" --ignore=tests\test_web_ui.py
.\.venv\Scripts\python.exe -m compileall agora
```

From the repository root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\export_protocol_schemas.py --check
git diff --check
```

Run frontend lint/build only when frontend files or shared API contracts change.
After a frontend static export exists, run the complete non-integration suite
including `tests/test_web_ui.py`.
