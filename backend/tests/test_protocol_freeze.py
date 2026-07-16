from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agora.protocol.gates import evaluate_gate
from agora.protocol.hashing import (
    canonical_json_bytes,
    native_snapshot_id,
    seal_model_payload,
)
from agora.protocol.invalidation import ArtifactChange, invalidate_approvals
from agora.protocol.memory import M2UpdateAction, RunOutcome, decide_m2_update
from agora.protocol.models import (
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactLocation,
    ContextPack,
    Evidence,
    GateDecision,
    GateRequirement,
    HandoffPack,
    NativeStateSnapshot,
    RunProtocolState,
    RunnerIsolationContract,
)
from agora.protocol.schema_registry import SCHEMA_MODELS, schema_document
from agora.protocol.repair import RepairAction, decide_schema_repair
from agora.protocol.runner import plan_cleanup_failure
from agora.protocol.state_machines import (
    GateStatus,
    StageStatus,
    TaskStatus,
    TransitionError,
    transition_gate,
    transition_stage,
    transition_task,
)


FIXTURES = Path(__file__).parent / "fixtures" / "protocol"
ROOT = Path(__file__).resolve().parents[2]


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _context_payload() -> dict:
    return {
        "schema_version": "1.0",
        "pack_id": "context-run-1",
        "project_id": "agora",
        "task_id": "task-protocol-freeze",
        "stage_key": "protocol-freeze",
        "run_id": "run-1",
        "generated_at": "2026-07-16T08:00:00+00:00",
        "stage_contract": {
            "contract_id": "contract-protocol-freeze",
            "title": "Freeze the protocol",
            "objective": "Create machine-verifiable protocol contracts.",
            "completion_conditions": [
                "Schemas match executable models.",
                "Gate and invalidation tests pass.",
            ],
        },
        "input_artifacts": [],
        "required_outputs": [
            {
                "output_id": "protocol-schemas",
                "kind": "json-schema",
                "schema_uri": None,
                "required": True,
            }
        ],
        "forbidden_constraints": [
            "Do not infer semantic success from process exit code.",
        ],
        "policies": [
            {
                "entry_id": "policy-review-gate",
                "version": 1,
                "sha256": "a" * 64,
                "title": "Independent review",
                "content": "Review is required before commit.",
                "source_ref": "AGENTS.md",
            }
        ],
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": {
            "max_seconds": 3600,
            "max_output_bytes": 1000000,
            "max_model_tokens": None,
            "max_cost_usd": None,
        },
    }


def _approval() -> Approval:
    return Approval.model_validate(
        {
            "schema_version": "1.0",
            "approval_id": "approval-requirements-v1",
            "project_id": "agora",
            "task_id": "task-protocol-freeze",
            "stage_key": "requirements",
            "gate_key": "requirements-approval",
            "repository_id": "agora-repository",
            "ref": "refs/heads/main",
            "commit_sha": "1" * 40,
            "artifact_versions": [
                {
                    "repository_id": "agora-repository",
                    "ref": "refs/heads/main",
                    "commit_sha": "1" * 40,
                    "path": "docs/requirements.md",
                    "sha256": "a" * 64,
                }
            ],
            "status": "active",
            "approved_by": "user",
            "approved_at": "2026-07-16T08:00:00+00:00",
            "stale_reason": None,
        }
    )


def test_context_pack_is_hash_sealed_and_rejects_tampering():
    payload = seal_model_payload(ContextPack, _context_payload())
    pack = ContextPack.model_validate(payload)

    assert pack.schema_version == "1.0"
    assert pack.content_sha256 == payload["content_sha256"]

    tampered = dict(payload)
    tampered["stage_key"] = "different-stage"
    with pytest.raises(ValidationError, match="content_sha256"):
        ContextPack.model_validate(tampered)


def test_protocol_rejects_unknown_fields_and_unsupported_major_versions():
    payload = seal_model_payload(ContextPack, _context_payload())
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ContextPack.model_validate(payload)

    unsupported = _context_payload()
    unsupported["schema_version"] = "2.0"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ContextPack.model_validate(seal_model_payload(ContextPack, unsupported))


def test_artifact_storage_and_utf8_hash_contracts():
    content = "中文と日本語 protocol"
    managed = Artifact.model_validate(
        {
            "schema_version": "1.0",
            "artifact_id": "artifact-managed",
            "project_id": "agora",
            "task_id": "task-protocol-freeze",
            "stage_key": "protocol-freeze",
            "producer": {
                "runtime": "codex",
                "run_id": "run-1",
                "stage_key": "protocol-freeze",
            },
            "kind": "protocol-document",
            "storage": "managed",
            "version": 1,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "media_type": "text/markdown",
            "content": content,
            "location": None,
            "created_at": "2026-07-16T08:00:00+00:00",
        }
    )
    assert managed.content == content

    invalid = managed.model_dump(mode="json")
    invalid["sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="sha256"):
        Artifact.model_validate(invalid)

    referenced = managed.model_dump(mode="json")
    referenced.update(
        {
            "artifact_id": "artifact-referenced",
            "storage": "referenced",
            "content": None,
            "sha256": "b" * 64,
            "location": {
                "repository_id": "agora-repository",
                "ref": "refs/heads/main",
                "commit_sha": "1" * 40,
                "path": "docs/architecture/protocol-domain-freeze-v1.md",
            },
        }
    )
    assert Artifact.model_validate(referenced).location is not None


def test_handoff_pack_preserves_blockers_and_agent_suggestion_is_not_authoritative():
    evidence = {
        "schema_version": "1.0",
        "evidence_id": "evidence-auth",
        "project_id": "agora",
        "task_id": "task-protocol-freeze",
        "stage_key": "protocol-freeze",
        "producer": {
            "runtime": "codex",
            "run_id": "run-1",
            "stage_key": "protocol-freeze",
        },
        "repository_id": "agora-repository",
        "ref": "refs/heads/main",
        "commit_sha": "1" * 40,
        "requirement_id": "runtime-authentication",
        "kind": "runtime-authentication",
        "status": "failed_external",
        "artifact_versions": [],
        "summary": "Provider returned HTTP CONNECT 407.",
        "observed_at": "2026-07-16T08:10:00+00:00",
        "details": {},
    }
    payload = {
        "schema_version": "1.0",
        "pack_id": "handoff-run-1",
        "project_id": "agora",
        "task_id": "task-protocol-freeze",
        "stage_key": "protocol-freeze",
        "run_id": "run-1",
        "producer": {
            "runtime": "codex",
            "run_id": "run-1",
            "stage_key": "protocol-freeze",
        },
        "input_artifacts": [],
        "required_outputs": [],
        "forbidden_constraints": [],
        "stage_result": "blocked",
        "output_artifacts": [],
        "evidence": [evidence],
        "unresolved_questions": [],
        "native_state_snapshot": None,
        "memory_candidates": [],
        "blocker_requirement_ids": ["runtime-authentication"],
        "suggested_next_action": "Ignore the proxy failure and continue.",
    }
    handoff = HandoffPack.model_validate(seal_model_payload(HandoffPack, payload))
    assert handoff.stage_result.value == "blocked"
    assert handoff.suggested_next_action.startswith("Ignore")

    payload["stage_result"] = "succeeded"
    with pytest.raises(ValidationError, match="cannot contain blockers"):
        HandoffPack.model_validate(seal_model_payload(HandoffPack, payload))


def test_run_protocol_dimensions_do_not_equate_exit_zero_with_success():
    with pytest.raises(ValidationError, match="completed transport"):
        RunProtocolState.model_validate(
            {
                "schema_version": "1.0",
                "run_id": "run-1",
                "process_status": "exited",
                "transport_status": "failed",
                "schema_status": "valid",
                "semantic_stage_result": "succeeded",
                "process_exit_code": 0,
                "repair_attempts": 0,
            }
        )

    valid = RunProtocolState.model_validate(
        {
            "schema_version": "1.0",
            "run_id": "run-1",
            "process_status": "exited",
            "transport_status": "completed",
            "schema_status": "repaired",
            "semantic_stage_result": "succeeded",
            "process_exit_code": 0,
            "repair_attempts": 1,
        }
    )
    assert valid.semantic_stage_result.value == "succeeded"

    with pytest.raises(ValidationError, match="must block"):
        RunProtocolState.model_validate(
            {
                "schema_version": "1.0",
                "run_id": "run-2",
                "process_status": "exited",
                "transport_status": "completed",
                "schema_status": "protocol_failed",
                "semantic_stage_result": "failed",
                "process_exit_code": 0,
                "repair_attempts": 1,
            }
        )


def test_workflow_polish_fixture_separates_external_failure_from_launcher_success():
    fixture = _load_fixture("workflow-polish-gate.json")
    requirements = [GateRequirement.model_validate(item) for item in fixture["requirements"]]
    evidence = [Evidence.model_validate(item) for item in fixture["evidence"]]

    result = evaluate_gate(requirements, evidence)

    assert result.decision.value == fixture["expected"]["decision"]
    assert result.blocker_requirement_ids == fixture["expected"]["blocker_requirement_ids"]
    assert result.next_safe_action == fixture["expected"]["next_safe_action"]
    status = {item.requirement_id: item.status.value for item in result.requirements}
    assert status == {
        "proxy-authentication": "failed_external",
        "aidlc-quality-gates": "missing",
        "launcher-mechanics": "passed",
    }


def test_gate_conflicting_current_evidence_fails_closed():
    fixture = _load_fixture("workflow-polish-gate.json")
    requirement = GateRequirement.model_validate(fixture["requirements"][0])
    failed = Evidence.model_validate(fixture["evidence"][1])
    passed_payload = failed.model_dump(mode="json")
    passed_payload["evidence_id"] = "evidence-proxy-retry"
    passed_payload["status"] = "passed"
    passed = Evidence.model_validate(passed_payload)

    result = evaluate_gate([requirement], [failed, passed])

    assert result.decision == GateDecision.BLOCK
    assert result.requirements[0].status.value == "failed_external"


def test_gate_ignores_unrelated_evidence_kinds_for_the_same_requirement():
    fixture = _load_fixture("workflow-polish-gate.json")
    requirement = GateRequirement.model_validate(fixture["requirements"][2])
    passed = Evidence.model_validate(fixture["evidence"][0])
    unrelated_payload = passed.model_dump(mode="json")
    unrelated_payload.update(
        {
            "evidence_id": "evidence-unrelated-kind",
            "kind": "different-kind",
            "status": "stale",
        }
    )
    unrelated = Evidence.model_validate(unrelated_payload)

    result = evaluate_gate([requirement], [passed, unrelated])

    assert result.decision == GateDecision.PASS
    assert result.requirements[0].status.value == "passed"
    assert result.requirements[0].evidence_ids == ["evidence-launcher"]


def test_gate_rejects_cross_ref_and_cross_commit_evidence():
    fixture = _load_fixture("workflow-polish-gate.json")
    requirement = GateRequirement.model_validate(fixture["requirements"][2])
    passed = Evidence.model_validate(fixture["evidence"][0])

    foreign_ref = passed.model_copy(update={"ref": "refs/heads/feature"})
    result = evaluate_gate([requirement], [foreign_ref])
    assert result.decision == GateDecision.BLOCK
    assert result.requirements[0].status.value == "missing"

    foreign_commit = passed.model_copy(update={"commit_sha": "2" * 40})
    result = evaluate_gate([requirement], [foreign_commit])
    assert result.decision == GateDecision.BLOCK
    assert result.requirements[0].status.value == "missing"


def test_deal_analysis_snapshot_is_branch_scoped_blocked_and_byte_deterministic():
    fixture = _load_fixture("deal-analysis-native-state.json")
    identity = fixture["identity"]
    blocker_codes = [
        item["code"] for item in fixture["conflicts"] if item["severity"] == "blocker"
    ]
    payload = {
        "schema_version": "1.0",
        "snapshot_id": native_snapshot_id(identity),
        **identity,
        "methodology": fixture["methodology"],
        "declared_native_stage": fixture["declared_native_stage"],
        "verified_native_stage": fixture["verified_native_stage"],
        "reconciliation_status": fixture["reconciliation_status"],
        "artifacts": [],
        "approval_ids": [],
        "conflicts": fixture["conflicts"],
        "gate_recommendation": {
            "decision": "block",
            "reasons": blocker_codes,
        },
    }
    first = NativeStateSnapshot.model_validate(
        seal_model_payload(NativeStateSnapshot, payload)
    )
    second = NativeStateSnapshot.model_validate(
        seal_model_payload(NativeStateSnapshot, payload)
    )

    assert [item.value for item in first.gate_recommendation.reasons] == fixture[
        "expected_blockers"
    ]
    assert first.verified_native_stage == "reconciliation_required"
    assert canonical_json_bytes(first) == canonical_json_bytes(second)

    other_ref = dict(identity)
    other_ref["canonical_ref"] = "refs/heads/feature"
    assert native_snapshot_id(other_ref) != first.snapshot_id

    other_methodology = dict(identity)
    other_methodology["methodology"] = "other-methodology"
    assert native_snapshot_id(other_methodology) != first.snapshot_id


def test_native_snapshot_canonicalizes_unordered_sets_before_hashing():
    fixture = _load_fixture("deal-analysis-native-state.json")
    identity = fixture["identity"]
    conflicts = list(reversed(fixture["conflicts"]))
    blocker_codes = [
        item["code"] for item in conflicts if item["severity"] == "blocker"
    ]
    payload = {
        "schema_version": "1.0",
        "snapshot_id": native_snapshot_id(identity),
        **identity,
        "declared_native_stage": fixture["declared_native_stage"],
        "verified_native_stage": fixture["verified_native_stage"],
        "reconciliation_status": fixture["reconciliation_status"],
        "artifacts": [],
        "approval_ids": ["approval-z", "approval-a"],
        "conflicts": conflicts,
        "gate_recommendation": {
            "decision": "block",
            "reasons": list(reversed(blocker_codes)),
        },
    }
    snapshot = NativeStateSnapshot.model_validate(
        seal_model_payload(NativeStateSnapshot, payload)
    )

    assert snapshot.approval_ids == ["approval-a", "approval-z"]
    assert [item.value for item in snapshot.gate_recommendation.reasons] == sorted(
        blocker_codes
    )
    assert [item.code.value for item in snapshot.conflicts] == sorted(
        item["code"] for item in conflicts
    )


def test_approval_hash_change_stales_approval_and_reopens_downstream_stages():
    approval = _approval()
    change = ArtifactChange(
        repository_id="agora-repository",
        ref="refs/heads/main",
        commit_sha="2" * 40,
        path="docs/requirements.md",
        sha256="b" * 64,
    )

    plan = invalidate_approvals(
        [approval],
        [change],
        stage_dependents={
            "requirements": {"design"},
            "design": {"build"},
        },
    )

    assert plan.stale_approval_ids == ["approval-requirements-v1"]
    assert plan.stale_gate_keys == ["requirements-approval"]
    assert plan.reopen_stage_keys == ["requirements", "design", "build"]
    assert plan.approvals[0].status == ApprovalStatus.STALE
    assert plan.attention_codes == [
        "approval_impact_analysis:approval-requirements-v1"
    ]

    unchanged_main = ArtifactChange(
        repository_id="agora-repository",
        ref="refs/heads/main",
        commit_sha="1" * 40,
        path="docs/requirements.md",
        sha256="a" * 64,
    )
    different_ref = change.model_copy(update={"ref": "refs/heads/feature"})
    untouched = invalidate_approvals([approval], [unchanged_main, different_ref])
    assert untouched.approvals[0].status == ApprovalStatus.ACTIVE
    assert untouched.stale_gate_keys == []


def test_approval_invalidation_catches_deletion_and_commit_change():
    approval = _approval()

    deleted = invalidate_approvals([approval], [])
    assert deleted.approvals[0].status == ApprovalStatus.STALE

    same_hash_new_commit = ArtifactChange(
        repository_id="agora-repository",
        ref="refs/heads/main",
        commit_sha="2" * 40,
        path="docs/requirements.md",
        sha256="a" * 64,
    )
    moved = invalidate_approvals([approval], [same_hash_new_commit])
    assert moved.approvals[0].status == ApprovalStatus.STALE


def test_approval_requires_artifact_bindings_from_the_same_commit():
    payload = _approval().model_dump(mode="json")
    payload["artifact_versions"][0]["commit_sha"] = "2" * 40

    with pytest.raises(ValidationError, match="repository, ref, and commit"):
        Approval.model_validate(payload)

    payload = _approval().model_dump(mode="json")
    payload["artifact_versions"][0]["path"] = r"C:\Windows\approval.md"
    with pytest.raises(ValidationError, match="repository-relative"):
        Approval.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Windows\System32\evil.dll",
        r"C:relative-drive-path.txt",
        r"\\server\share\artifact.md",
        "/absolute/artifact.md",
        "../outside.md",
        "docs/../outside.md",
        "docs//artifact.md",
    ],
)
def test_repository_paths_reject_absolute_traversal_and_noncanonical_forms(path):
    with pytest.raises(ValidationError, match="repository-relative|segments"):
        ArtifactLocation.model_validate(
            {
                "repository_id": "agora-repository",
                "ref": "refs/heads/main",
                "commit_sha": "1" * 40,
                "path": path,
            }
        )

    with pytest.raises(ValidationError, match="repository-relative|segments"):
        ArtifactChange(
            repository_id="agora-repository",
            ref="refs/heads/main",
            commit_sha="1" * 40,
            path=path,
            sha256="a" * 64,
        )


def test_repository_paths_normalize_backslashes():
    location = ArtifactLocation.model_validate(
        {
            "repository_id": "agora-repository",
            "ref": "refs/heads/main",
            "commit_sha": "1" * 40,
            "path": r"docs\architecture\protocol.md",
        }
    )
    assert location.path == "docs/architecture/protocol.md"


@pytest.mark.parametrize(
    ("outcome", "gate", "expected"),
    [
        (RunOutcome.RUNNING, None, M2UpdateAction.CANDIDATE_ONLY),
        (
            RunOutcome.FAILED,
            GateDecision.BLOCK,
            M2UpdateAction.PRESERVE_VERIFIED_APPEND_ATTEMPT,
        ),
        (
            RunOutcome.CANCELLED,
            None,
            M2UpdateAction.PRESERVE_VERIFIED_APPEND_ATTEMPT,
        ),
        (
            RunOutcome.PROTOCOL_FAILED,
            GateDecision.BLOCK,
            M2UpdateAction.PRESERVE_VERIFIED_APPEND_ATTEMPT,
        ),
        (
            RunOutcome.SUCCEEDED,
            GateDecision.BLOCK,
            M2UpdateAction.PUBLISH_UNVERIFIED_DRAFT,
        ),
        (
            RunOutcome.SUCCEEDED,
            GateDecision.PASS,
            M2UpdateAction.PUBLISH_VERIFIED_ATOMIC,
        ),
    ],
)
def test_m2_publication_rules(outcome, gate, expected):
    assert decide_m2_update(outcome, gate) == expected


def test_runner_isolation_requires_per_run_writable_roots_and_confined_workspace():
    contract = RunnerIsolationContract.model_validate(
        {
            "schema_version": "1.0",
            "platform": "windows",
            "run_id": "run-1",
            "run_root": r"E:\Agora\runs\run-1",
            "workspace": r"E:\Processing\projects\Agora\.agora\workspaces\codex",
            "allowed_workspace_roots": [r"E:\Processing\projects"],
            "home_dir": r"E:\Agora\runs\run-1\home",
            "temp_dir": r"E:\Agora\runs\run-1\temp",
            "cache_dir": r"E:\Agora\runs\run-1\cache",
            "config_dir": r"E:\Agora\runs\run-1\config",
            "credential_refs": ["credential://codex/default"],
            "serialized_global_operations": ["git-credential-helper-init"],
            "recovery_marker": r"E:\Agora\runs\run-1\recovery.json",
        }
    )
    assert contract.credential_refs == ["credential://codex/default"]

    escaped = contract.model_dump(mode="json")
    escaped["home_dir"] = r"C:\Users\shared"
    with pytest.raises(ValidationError, match="within run_root"):
        RunnerIsolationContract.model_validate(escaped)

    workspace_escape = contract.model_dump(mode="json")
    workspace_escape["workspace"] = r"C:\Windows"
    with pytest.raises(ValidationError, match="allowed workspace root"):
        RunnerIsolationContract.model_validate(workspace_escape)

    invalid_marker = contract.model_dump(mode="json")
    invalid_marker["recovery_marker"] = r"E:\Agora\runs\run-1\recovery"
    with pytest.raises(ValidationError, match="recovery_marker"):
        RunnerIsolationContract.model_validate(invalid_marker)

    traversing_credential = contract.model_dump(mode="json")
    traversing_credential["credential_refs"] = ["credential://codex/../admin"]
    with pytest.raises(ValidationError, match="path traversal"):
        RunnerIsolationContract.model_validate(traversing_credential)

    posix = contract.model_dump(mode="json")
    posix.update(
        {
            "run_root": "/tmp/agora/run-1",
            "workspace": "/work/agora",
            "allowed_workspace_roots": ["/work"],
            "home_dir": "/tmp/agora/run-1/home",
            "temp_dir": "/tmp/agora/run-1/temp",
            "cache_dir": "/tmp/agora/run-1/cache",
            "config_dir": "/tmp/agora/run-1/config",
            "recovery_marker": "/tmp/agora/run-1/recovery.json",
        }
    )
    with pytest.raises(ValidationError, match="absolute Windows paths"):
        RunnerIsolationContract.model_validate(posix)

    cleanup = plan_cleanup_failure(contract, "cache directory remained locked")
    assert cleanup.recovery_marker.endswith("recovery.json")
    assert cleanup.attention_code == "runner_cleanup_failed:run-1"
    assert cleanup.preserve_workspace is True


def test_evidence_details_are_json_and_bounded():
    fixture = _load_fixture("workflow-polish-gate.json")
    payload = fixture["evidence"][0]

    oversized = dict(payload)
    oversized["details"] = {"log": "x" * (64 * 1024)}
    with pytest.raises(ValidationError, match="64 KiB"):
        Evidence.model_validate(oversized)

    too_deep = dict(payload)
    nested: dict = {}
    cursor = nested
    for index in range(14):
        cursor["child"] = {}
        cursor = cursor["child"]
    too_deep["details"] = nested
    with pytest.raises(ValidationError, match="nesting limit"):
        Evidence.model_validate(too_deep)

    too_wide = dict(payload)
    too_wide["details"] = {f"key-{index}": index for index in range(2001)}
    with pytest.raises(ValidationError, match="node limit"):
        Evidence.model_validate(too_wide)


def test_schema_repair_allows_one_format_only_attempt_then_fails_closed():
    first = decide_schema_repair(schema_valid=False, repair_attempts=0)
    assert first.action == RepairAction.REQUEST_FORMAT_REPAIR
    assert first.repair_attempts == 1
    assert first.semantic_changes_allowed is False
    assert first.attention_required is False

    second = decide_schema_repair(schema_valid=False, repair_attempts=1)
    assert second.action == RepairAction.PROTOCOL_FAILED
    assert second.repair_attempts == 1
    assert second.semantic_changes_allowed is False
    assert second.attention_required is True


def test_task_stage_and_gate_transitions_are_explicit_and_fail_closed():
    assert transition_task(TaskStatus.BACKLOG, TaskStatus.READY) == TaskStatus.READY
    assert transition_task(TaskStatus.COMPLETED, TaskStatus.ACTIVE) == TaskStatus.ACTIVE
    assert transition_stage(StageStatus.COMPLETED, StageStatus.READY) == StageStatus.READY
    assert transition_gate(GateStatus.PASSED, GateStatus.STALE) == GateStatus.STALE

    with pytest.raises(TransitionError):
        transition_task(TaskStatus.BACKLOG, TaskStatus.COMPLETED)
    with pytest.raises(TransitionError):
        transition_stage(StageStatus.PENDING, StageStatus.COMPLETED)
    with pytest.raises(TransitionError):
        transition_gate(GateStatus.PASSED, GateStatus.PASSED)


def test_checked_in_json_schemas_match_executable_models():
    schema_dir = ROOT / "docs" / "architecture" / "schemas"
    for name, model in SCHEMA_MODELS.items():
        checked_in = json.loads(
            (schema_dir / f"{name}.schema.json").read_text(encoding="utf-8")
        )
        assert checked_in == schema_document(name, model)
