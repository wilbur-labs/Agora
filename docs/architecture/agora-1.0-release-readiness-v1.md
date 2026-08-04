# Agora 1.0 release readiness v1

Status: frozen release-closeout boundary

## Release meaning

Agora 1.0 is the local-first Task delivery control plane whose authoritative
mainline is:

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

The 1.0 release means the repository can be installed from its lock files,
build and serve the authenticated static Control Plane, execute the
deterministic no-provider formal acceptance path, and expose the reviewed CLI
and HTTP authority boundaries. It does not mean that every installed native AI
subscription is available or that Agora can guarantee provider quality,
serviceability, price, or hard Token limits.

## Required closeout

The release closeout must:

1. publish one consistent `1.0.0` version through the Python package, API, and
   frontend package metadata;
2. replace migration-era and corrupted user-facing README content with valid
   UTF-8 English, Chinese, and Japanese entry pages;
3. provide a practical Chinese tutorial covering locked installation, local
   startup, authentication, deterministic acceptance, Task CLI operation,
   consultation disposition, recovery, and shutdown;
4. reconcile the current requirements alignment with the reviewed mainline
   instead of leaving completed work under a stale `Still required` heading;
5. prove the locked backend/frontend environments, schemas, tests, static
   export, deterministic acceptance, and an isolated live HTTP startup; and
6. record independent Claude Code review and a durable final checkpoint.

## Preserved boundaries

- The retired 0.5 autonomous Council remains unavailable and fail closed.
- `agora task consult` invokes only the runtime already pinned to the current
  Stage. Its result remains an advisory candidate until a human adopts or
  rejects it.
- Kiro project/research/orchestration/execution configuration and `.kiro/` user
  data remain intact even while the external Kiro contract is unavailable.
- Codex, Claude Code, and Kiro availability is observed, never fabricated.
- Missing or unavailable native runtimes block their pinned Stage; Agora does
  not silently substitute another runtime.
- Dynamic roles, arbitrary local-model adapters, runtime substitution, POSIX
  embedded-runner isolation, and large shared-repository invalidation are
  post-1.0 enhancements. This release closeout does not implement them.
- Docker packaging remains supported, but an unavailable local Docker daemon
  is an external environment condition, not evidence that the direct Windows
  startup path failed.

## Release acceptance

The release is acceptable only when all current checked-in non-integration
tests pass (a deterministic mutually exclusive shard is allowed for the known
long methodology reconstruction file), frontend tests/lint/build pass,
protocol schemas match, `compileall` and `git diff --check` pass, deterministic
Task acceptance reports `provider_or_model_called=false`, and an isolated live
server returns:

- `200` and version `1.0.0` from `/health`;
- the static Task Control Plane from `/` and `/control-plane`;
- `401` without a bearer for the protected Task index;
- an authenticated bounded Task index with the configured bearer; and
- `410 legacy_council_retired` from a retired Council API route.

No release claim may depend on an autonomous discussion, a model-generated
approval, a hidden provider call, or deletion of user-owned untracked files.
