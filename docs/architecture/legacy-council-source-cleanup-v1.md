# Legacy Council source cleanup v1

Status: frozen cleanup boundary

## Purpose

Agora 1.0 is the authoritative Task delivery control plane described by the
protocol domain freeze. The retired 0.5 autonomous Council is already
unreachable from the CLI, HTTP API, and static web application. This increment
removes that unreachable implementation so the repository does not imply that
AI-to-AI discussion remains a supported execution path.

This cleanup changes no Task, Stage, Run, Gate, Approval, consultation, or
candidate-disposition semantics.

## Delete boundary

The following tracked implementation is Council-only and has no imports from
the active Task mainline:

- `backend/agora/agents/`, `context/`, `models/`, `memory/`, `skills/`,
  `tools/`, and `sandbox/`;
- the unregistered legacy API modules `_state.py`, `agents.py`, `artifacts.py`,
  `chat.py`, `extras.py`, `sessions.py`, and `sessions_db.py`;
- Council-only tests, mock providers, and the runnable `scripts/demo.sh`;
- the unreachable chat/session/artifact React components, hook, types, and API
  client functions;
- direct-provider, Council, agent, skill-store, sandbox, and legacy web-tool
  configuration; and
- dependencies and Docker mounts used only by that implementation.

The removal is covered by Git history. Historical 0.5 release notes and
changelog entries may remain as provenance, but they are not executable
product instructions.

## Retain boundary

The cleanup must preserve:

- the central HTTP `410 legacy_council_retired` interception and UI redirects;
- the deterministic `agora task` CLI, protocol models, Control Plane API,
  static authenticated console, operational execution, research dispatch, and
  workflow reconciliation;
- the single-runtime, explicit `agora task consult` path whose immutable output
  remains advisory until a human adopts or rejects it;
- Codex, Claude Code, and Kiro CLI runtime adapters, route pins, workspaces,
  result normalization, and capability observation;
- Kiro configuration and `.kiro/` user data, even while Kiro is temporarily not
  invoked for review;
- the generic `memory.data_dir` compatibility key while active Task storage
  still uses it as a data-directory fallback;
- optional embedding support; and
- future configurable-role and local-runtime extension points. No dynamic role
  or local-model feature is implemented in this cleanup.

## Fail-closed acceptance

The increment is acceptable only when:

1. no active tracked source imports the deleted Council namespaces;
2. retired API roots still return the stable `410` result for every method and
   retired UI paths still redirect to `/control-plane`;
3. the static frontend builds without the legacy chat bundle;
4. Kiro remains present in project, research, orchestration, and execution
   runtime configuration;
5. deterministic Task acceptance, protocol schema checks, and the complete
   non-integration suite pass; and
6. an independent Claude Code review reports no HIGH or MEDIUM correctness,
   safety, regression, or boundary issue.

This increment does not authorize autonomous AI discussion, runtime
substitution, a release-version change, or deletion of user-owned untracked
files.
