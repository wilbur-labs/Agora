"""Build one bounded, sealed Context Pack for formal protocol orchestration."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import Field

from agora.protocol.hashing import canonical_json_bytes, canonical_sha256, seal_model_payload
from agora.protocol.models import (
    Artifact,
    ContextEntry,
    ContextPack,
    GateRequirement,
    PinnedRuntimePreflightDecision,
    RequiredOutput,
    RequirementSeverity,
    RunBudget,
    ProtocolModel,
    StableId,
    StageContract,
)
from agora.tasks.models import TaskManifest

from .contracts import RoleAssignment, StageTaskContract, TaskContract, contract_sha256
from .models import OrchestrationStage, RoutingPolicyDecision, TaskDecision


PROTOCOL_PROMPT_LIMIT = 24 * 1024
CONTEXT_ENTRY_CONTENT_LIMIT = 20_000


class RepositoryRevision(ProtocolModel):
    """One immutable repository scope used by every Gate requirement."""

    repository_id: StableId
    ref: str = Field(min_length=1, max_length=20_000)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{7,64}$")


class ProtocolRunDefinition(ProtocolModel):
    context_pack: ContextPack
    gate_key: StableId
    gate_requirements: list[GateRequirement] = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=PROTOCOL_PROMPT_LIMIT)


def resolve_git_revision(root: Path, *, repository_id: str) -> RepositoryRevision:
    """Resolve the exact Git ref and commit without invoking a shell."""

    def git(*args: str, allow_empty: bool = False) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Repository revision is unavailable") from exc
        value = completed.stdout.strip()
        if completed.returncode != 0 or (not value and not allow_empty):
            raise ValueError("Project root must be a readable Git repository")
        return value

    commit_sha = git("rev-parse", "--verify", "HEAD").lower()
    ref = git("rev-parse", "--symbolic-full-name", "HEAD")
    if ref == "HEAD":
        ref = f"refs/commits/{commit_sha}"
    if git("status", "--porcelain=v1", allow_empty=True):
        raise ValueError(
            "Formal protocol orchestration requires a clean Git worktree so "
            "Evidence can bind to the resolved commit"
        )
    return RepositoryRevision(
        repository_id=repository_id,
        ref=ref,
        commit_sha=commit_sha,
    )


def build_protocol_run_definition(
    *,
    task: TaskManifest,
    contract: TaskContract,
    stage: OrchestrationStage,
    run_id: str,
    revision: RepositoryRevision,
    prior_artifacts: Sequence[Artifact],
    decisions: Sequence[TaskDecision],
    routing_policy: RoutingPolicyDecision,
    runtime_preflight: PinnedRuntimePreflightDecision,
    generated_at: datetime | str,
    timeout_seconds: int,
    max_output_bytes: int = 1_000_000,
) -> ProtocolRunDefinition:
    """Project reviewed orchestration inputs into the frozen protocol contract."""

    stage_contract = _contract_stage(contract, stage.stage_key)
    role = next(item for item in contract.roles if item.role_id == stage_contract.role_id)
    if role.runtime != stage.adapter:
        raise ValueError("Task contract runtime does not match the claimed Stage adapter")
    if (
        not routing_policy.dispatchable
        or routing_policy.task_id != task.task_id
        or routing_policy.project_id != task.project_id
        or routing_policy.stage_key != stage.stage_key
        or routing_policy.role != stage.role
        or routing_policy.pinned_runtime != stage.adapter
    ):
        raise ValueError("Routing policy does not authorize the pinned Stage assignment")
    if (
        not runtime_preflight.allowed
        or runtime_preflight.task_id != task.task_id
        or runtime_preflight.project_id != task.project_id
        or runtime_preflight.run_id != run_id
        or runtime_preflight.stage_key != stage.stage_key
        or runtime_preflight.role != stage.role
        or runtime_preflight.pinned_runtime != stage.adapter
        or runtime_preflight.routing_policy_decision_id
        != routing_policy.decision_id
        or runtime_preflight.routing_policy_decision_sha256
        != routing_policy.content_sha256
    ):
        raise ValueError("Runtime preflight does not authorize the pinned Stage launch")
    if task.project_id != revision.repository_id:
        raise ValueError("Repository identity must match the Task project")

    previous_stage_keys = {
        item.stage_key
        for item in contract.workflow[: contract.workflow.index(stage_contract)]
    }
    if stage.attempt_count:
        previous_stage_keys.add(stage.stage_key)
    inputs = _latest_prior_artifacts(prior_artifacts, previous_stage_keys)
    input_refs = [item.version_ref() for item in inputs]
    required_outputs = [
        RequiredOutput(
            output_id=(
                "artifact:"
                + canonical_sha256(
                    {
                        "task_id": task.task_id,
                        "stage_key": stage.stage_key,
                        "run_id": run_id,
                        "artifact_template_id": item.artifact_id,
                    }
                )[:32]
            ),
            kind=item.kind,
            required=True,
        )
        for item in stage_contract.required_artifacts
    ]
    requirements = _gate_requirements(stage_contract, revision)
    contract_hash = contract_sha256(contract)
    policies = [
        _context_entry(
            prefix="policy",
            title="Pinned Task and Stage contract",
            content=_compact_json(
                _task_contract_projection(
                    contract=contract,
                    contract_hash=contract_hash,
                    role=role,
                )
            ),
            source_ref=f"task-contract:{contract.contract_id}:{contract_hash}",
        ),
        _context_entry(
            prefix="policy",
            title="Repository and Gate evidence binding",
            content=json.dumps(
                {
                    "repository_id": revision.repository_id,
                    "ref": revision.ref,
                    "commit_sha": revision.commit_sha,
                    "gate_requirements": [
                        item.model_dump(mode="json") for item in requirements
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_ref=(
                f"git:{revision.repository_id}:{revision.ref}:{revision.commit_sha}"
            ),
        ),
        _context_entry(
            prefix="policy",
            title="Explainable pinned runtime and protected review budget",
            content=_compact_json(_routing_policy_projection(routing_policy)),
            source_ref=(
                f"routing-policy:{routing_policy.decision_id}:"
                f"{routing_policy.content_sha256}"
            ),
        ),
        _context_entry(
            prefix="policy",
            title="Fresh pinned native runtime preflight",
            content=_compact_json(_runtime_preflight_projection(runtime_preflight)),
            source_ref=(
                f"runtime-preflight:{runtime_preflight.decision_id}:"
                f"{runtime_preflight.content_sha256}"
            ),
        ),
    ]
    decision_memory = [
        _context_entry(
            prefix="decision",
            title=f"Task decision {item.decision_key}@{item.version}",
            content=json.dumps(
                {
                    "decision_key": item.decision_key,
                    "decision_value": item.decision_value,
                    "rationale": item.rationale,
                    "version": item.version,
                    "actor": item.actor,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_ref=f"task-decision:{item.decision_id}:{item.decision_sha256}",
        )
        for item in decisions
    ]
    artifact_memory = {
        item.artifact_id: _artifact_reference_context(
            item,
            reason=(
                "external_reference"
                if item.content is None
                else "prompt_bound"
            ),
        )
        for item in inputs
    }

    stage_contract_id = canonical_sha256(
        {"contract_sha256": contract_hash, "stage_key": stage.stage_key}
    )
    payload = {
        "schema_version": "1.0",
        "pack_id": f"context:{run_id}",
        "project_id": task.project_id,
        "task_id": task.task_id,
        "stage_key": stage.stage_key,
        "run_id": run_id,
        "generated_at": generated_at,
        "stage_contract": StageContract(
            contract_id=f"stage-contract:{stage_contract_id[:32]}",
            title=stage_contract.title,
            objective=stage_contract.objective,
            completion_conditions=stage_contract.completion_conditions,
        ),
        "input_artifacts": input_refs,
        "required_outputs": required_outputs,
        "forbidden_constraints": [
            *contract.forbidden_constraints,
            "Do not write authoritative Task, Stage, Gate, Approval, or Evidence state.",
            "Do not infer semantic success from process exit code.",
            "Do not return a full prior transcript as the Handoff contract.",
        ],
        "policies": policies,
        "project_knowledge": [],
        "user_preferences": [],
        "budget": RunBudget(
            max_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_model_tokens=routing_policy.current_run_token_reservation,
            max_cost_usd=routing_policy.current_run_cost_reservation_usd,
        ),
    }
    gate_key = f"gate:{stage.stage_key}"

    def build_context_pack() -> ContextPack:
        task_memory = [
            *decision_memory,
            *(artifact_memory[item.artifact_id] for item in inputs),
        ]
        return ContextPack.model_validate(
            seal_model_payload(
                ContextPack,
                {**payload, "task_memory": task_memory},
            )
        )

    context_pack = build_context_pack()
    stage_sequence = {
        item.stage_key: index
        for index, item in enumerate(contract.workflow)
    }
    materialization_order = sorted(
        inputs,
        key=lambda item: (
            -stage_sequence[item.stage_key],
            item.artifact_id,
        ),
    )
    for artifact in materialization_order:
        if artifact.content is None:
            continue
        if len(artifact.content.encode("utf-8")) > CONTEXT_ENTRY_CONTENT_LIMIT:
            artifact_memory[artifact.artifact_id] = _artifact_reference_context(
                artifact,
                reason="content_entry_bound",
            )
            context_pack = build_context_pack()
            continue
        materialized = _artifact_context(artifact)
        artifact_memory[artifact.artifact_id] = materialized
        candidate = build_context_pack()
        if not _protocol_prompt_fits(
            context_pack=candidate,
            runtime=role.runtime,
            requirements=requirements,
        ):
            artifact_memory[artifact.artifact_id] = _artifact_reference_context(
                artifact,
                reason="prompt_bound",
            )
            continue
        context_pack = candidate

    prompt = _build_protocol_prompt(
        context_pack=context_pack,
        runtime=role.runtime,
        requirements=requirements,
    )
    return ProtocolRunDefinition(
        context_pack=context_pack,
        gate_key=gate_key,
        gate_requirements=requirements,
        prompt=prompt,
    )


def _contract_stage(contract: TaskContract, stage_key: str) -> StageTaskContract:
    try:
        return next(item for item in contract.workflow if item.stage_key == stage_key)
    except StopIteration as exc:
        raise ValueError("Current Stage is absent from the pinned Task contract") from exc


def _latest_prior_artifacts(
    artifacts: Sequence[Artifact],
    previous_stage_keys: set[str],
) -> list[Artifact]:
    latest: dict[str, Artifact] = {}
    for artifact in artifacts:
        if artifact.stage_key not in previous_stage_keys:
            continue
        current = latest.get(artifact.artifact_id)
        if current is None or artifact.version > current.version:
            latest[artifact.artifact_id] = artifact
    return [latest[key] for key in sorted(latest)]


def _artifact_context(artifact: Artifact) -> ContextEntry:
    if artifact.content is None:
        return _artifact_reference_context(artifact, reason="external_reference")
    content = artifact.content
    return _context_entry(
        prefix="artifact",
        title=f"Prior formal Artifact {artifact.artifact_id}@{artifact.version}",
        content=content,
        source_ref=(
            f"artifact:{artifact.artifact_id}:{artifact.version}:{artifact.sha256}"
        ),
    )


def _artifact_reference_context(
    artifact: Artifact,
    *,
    reason: str = "prompt_bound",
) -> ContextEntry:
    content = _compact_json(
        {
            "artifact_version": artifact.version_ref().model_dump(mode="json"),
            "materialization": {
                "status": "reference_only",
                "reason": reason,
                "content_utf8_bytes": (
                    len(artifact.content.encode("utf-8"))
                    if artifact.content is not None
                    else None
                ),
            },
        }
    )
    return _context_entry(
        prefix="artifact",
        title=f"Prior formal Artifact reference {artifact.artifact_id}@{artifact.version}",
        content=content,
        source_ref=(
            f"artifact:{artifact.artifact_id}:{artifact.version}:{artifact.sha256}"
        ),
    )


def _context_entry(*, prefix: str, title: str, content: str, source_ref: str) -> ContextEntry:
    digest = canonical_sha256(
        {"title": title, "content": content, "source_ref": source_ref}
    )
    return ContextEntry(
        entry_id=f"{prefix}:{digest[:32]}",
        version=1,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        title=title[:300],
        content=content,
        source_ref=source_ref,
    )


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _task_contract_projection(
    *,
    contract: TaskContract,
    contract_hash: str,
    role: RoleAssignment,
) -> dict[str, object]:
    return {
        "task_contract_id": contract.contract_id,
        "task_contract_schema_version": contract.schema_version,
        "task_contract_sha256": contract_hash,
        "task_goal": contract.goal,
        "role": {
            "role_id": role.role_id,
            "runtime": role.runtime,
            "responsibilities": role.responsibilities,
            "independent_from": role.independent_from,
        },
    }


def _routing_policy_projection(
    policy: RoutingPolicyDecision,
) -> dict[str, object]:
    cost_budget = {
        "task_cost_budget_usd": policy.task_cost_budget_usd,
        "settled_cost_debit_usd": policy.settled_cost_debit_usd,
        "active_cost_reservations_usd": policy.active_cost_reservations_usd,
        "available_cost_before_dispatch_usd": (
            policy.available_cost_before_dispatch_usd
        ),
        "current_run_cost_reservation_usd": (
            policy.current_run_cost_reservation_usd
        ),
        "protected_future_reviewer_cost_usd": (
            policy.protected_future_reviewer_cost_usd
        ),
    }
    projection = {
        "schema_version": policy.schema_version,
        "decision_id": policy.decision_id,
        "content_sha256": policy.content_sha256,
        "policy_sha256": policy.policy_sha256,
        "route": {
            "stage_key": policy.stage_key,
            "role": policy.role,
            "pinned_runtime": policy.pinned_runtime,
        },
        "task_risk": policy.task_risk,
        "required_capabilities": policy.required_capabilities,
        "runtime_capabilities": policy.runtime_capabilities,
        "required_reviewers": policy.required_reviewers,
        "reviewers": [
            {
                "runtime": item.runtime,
                "role": item.role,
                "stage_key": item.stage_key,
                "independent_from_roles": item.independent_from_roles,
            }
            for item in policy.reviewer_assignments
        ],
        "token_budget": {
            "task_token_budget": policy.task_token_budget,
            "settled_token_debit": policy.settled_token_debit,
            "available_tokens_before_dispatch": (
                policy.available_tokens_before_dispatch
            ),
            "current_run_token_reservation": policy.current_run_token_reservation,
            "protected_future_reviewer_tokens": (
                policy.protected_future_reviewer_tokens
            ),
        },
        "dispatchable": policy.dispatchable,
        "blockers": policy.blockers,
    }
    if any(value is not None for value in cost_budget.values()):
        projection["cost_budget"] = cost_budget
    else:
        projection["cost_measurement"] = "unavailable"
    return projection


def _runtime_preflight_projection(
    decision: PinnedRuntimePreflightDecision,
) -> dict[str, object]:
    return {
        "schema_version": decision.schema_version,
        "decision_id": decision.decision_id,
        "content_sha256": decision.content_sha256,
        "route": {
            "stage_key": decision.stage_key,
            "role": decision.role,
            "pinned_runtime": decision.pinned_runtime,
        },
        "routing_policy": {
            "decision_id": decision.routing_policy_decision_id,
            "content_sha256": decision.routing_policy_decision_sha256,
        },
        "observation": {
            "content_sha256": decision.capability_observation_sha256,
            "runtime_registry_sha256": decision.runtime_registry_sha256,
            "runtime_command_sha256": decision.runtime_command_sha256,
            "resolved_runtime_command_sha256": (
                decision.resolved_runtime_command_sha256
            ),
            "capability_declaration_sha256": (
                decision.capability_declaration_sha256
            ),
            "installation_status": decision.installation_status,
            "version": decision.version,
            "version_status": decision.version_status,
            "model_availability": decision.model_availability,
            "routing_authority": (
                decision.capability_observation.routing_authority
            ),
        },
        "checks": {
            item.check: item.satisfied
            for item in decision.checks
        },
        "allowed": decision.allowed,
        "blockers": decision.blockers,
        "route_selection_authority": decision.route_selection_authority,
        "runtime_substitution_allowed": decision.runtime_substitution_allowed,
        "provider_serviceability_verified": (
            decision.provider_serviceability_verified
        ),
    }


def _gate_requirements(
    stage: StageTaskContract,
    revision: RepositoryRevision,
) -> list[GateRequirement]:
    evidence = {item.requirement_id: item for item in stage.required_evidence}
    return [
        GateRequirement(
            requirement_id=item.requirement_id,
            title=evidence[item.requirement_id].description[:300],
            repository_id=revision.repository_id,
            ref=revision.ref,
            commit_sha=revision.commit_sha,
            evidence_kind=evidence[item.requirement_id].kind,
            severity=RequirementSeverity(item.severity),
            priority=item.priority,
            failure_action=item.failure_action,
        )
        for item in stage.gate_requirements
    ]


def _build_protocol_prompt(
    *,
    context_pack: ContextPack,
    runtime: str,
    requirements: Sequence[GateRequirement],
) -> str:
    prompt = _render_protocol_prompt(
        context_pack=context_pack,
        runtime=runtime,
        requirements=requirements,
    )
    if len(prompt.encode("utf-8")) > PROTOCOL_PROMPT_LIMIT:
        raise ValueError("Formal protocol prompt exceeds the 24 KiB dispatch bound")
    return prompt


def _render_protocol_prompt(
    *,
    context_pack: ContextPack,
    runtime: str,
    requirements: Sequence[GateRequirement],
) -> str:
    context_json = canonical_json_bytes(context_pack).decode("utf-8")
    requirement_json = json.dumps(
        [item.model_dump(mode="json") for item in requirements],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prompt = f"""You are the {runtime} runtime for one formal Agora protocol Run.

Operate within the sealed Context Pack below. It is the complete handoff contract,
not a transcript. Work read-only unless that Context explicitly authorizes mutation.
Agora alone writes authoritative Task, Stage, Gate, Artifact, Evidence, and Approval state.

Return ONLY one UTF-8 JSON object matching Agora HandoffPack schema version 1.0.
Do not add prose. One whole-document ```json fence is repairable but exact JSON is preferred.

The Handoff must exactly echo project_id, task_id, stage_key, run_id,
input_artifacts, required_outputs, and forbidden_constraints from the Context Pack.
Set producer.runtime to {runtime!r}; producer.run_id and producer.stage_key must match the Run.
For stage_result=succeeded, emit every required output Artifact with the exact output_id/kind.
Managed Artifact sha256 is SHA-256 of its UTF-8 content. Evidence intended for the Gate
must use the exact repository/ref/commit/requirement/kind binding listed below.
Unknowns or unmet requirements must be represented as blockers; exit code zero is not success.
Compute Handoff content_sha256 over canonical JSON (UTF-8, sorted keys, compact separators),
excluding only the top-level content_sha256 field.

FORMAL GATE REQUIREMENTS:
{requirement_json}

SEALED CONTEXT PACK (canonical JSON):
{context_json}
END SEALED CONTEXT PACK
"""
    return prompt


def _protocol_prompt_fits(
    *,
    context_pack: ContextPack,
    runtime: str,
    requirements: Sequence[GateRequirement],
) -> bool:
    prompt = _render_protocol_prompt(
        context_pack=context_pack,
        runtime=runtime,
        requirements=requirements,
    )
    return len(prompt.encode("utf-8")) <= PROTOCOL_PROMPT_LIMIT
