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
    RequiredOutput,
    RequirementSeverity,
    RunBudget,
    ProtocolModel,
    StableId,
    StageContract,
)
from agora.tasks.models import TaskManifest

from .contracts import StageTaskContract, TaskContract, contract_sha256
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
            content=json.dumps(
                {
                    "task_contract_id": contract.contract_id,
                    "task_contract_schema_version": contract.schema_version,
                    "task_contract_sha256": contract_hash,
                    "task_goal": contract.goal,
                    "role": role.model_dump(mode="json"),
                    "stage": stage_contract.model_dump(mode="json"),
                    "acceptance_criteria": contract.acceptance_criteria,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
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
            content=json.dumps(
                routing_policy.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            source_ref=(
                f"routing-policy:{routing_policy.decision_id}:"
                f"{routing_policy.content_sha256}"
            ),
        ),
    ]
    task_memory = [
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
    task_memory.extend(_artifact_context(item) for item in inputs)

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
        "task_memory": task_memory,
        "project_knowledge": [],
        "user_preferences": [],
        "budget": RunBudget(
            max_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_model_tokens=routing_policy.current_run_token_reservation,
            max_cost_usd=routing_policy.current_run_cost_reservation_usd,
        ),
    }
    context_pack = ContextPack.model_validate(seal_model_payload(ContextPack, payload))
    gate_key = f"gate:{stage.stage_key}"
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
        content = json.dumps(
            {"artifact_version": artifact.version_ref().model_dump(mode="json")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        content = artifact.content
    if len(content) > CONTEXT_ENTRY_CONTENT_LIMIT:
        raise ValueError(
            f"Prior Artifact {artifact.artifact_id}@{artifact.version} exceeds the "
            "bounded Context entry; publish a smaller formal handoff Artifact"
        )
    return _context_entry(
        prefix="artifact",
        title=f"Prior formal Artifact {artifact.artifact_id}@{artifact.version}",
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
    if len(prompt.encode("utf-8")) > PROTOCOL_PROMPT_LIMIT:
        raise ValueError("Formal protocol prompt exceeds the 24 KiB dispatch bound")
    return prompt
