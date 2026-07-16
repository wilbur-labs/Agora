"""Deterministic fail-closed Gate evaluation."""
from __future__ import annotations

from collections import defaultdict

from .models import (
    Evidence,
    EvidenceStatus,
    GateDecision,
    GateEvaluation,
    GateRequirement,
    RequirementEvaluation,
    RequirementSeverity,
)


_FAIL_CLOSED_PRECEDENCE = {
    EvidenceStatus.STALE: 0,
    EvidenceStatus.MISSING: 1,
    EvidenceStatus.FAILED_PRODUCT: 2,
    EvidenceStatus.FAILED_EXTERNAL: 3,
    EvidenceStatus.PASSED: 4,
}


def evaluate_gate(
    requirements: list[GateRequirement],
    evidence: list[Evidence],
) -> GateEvaluation:
    """Evaluate active Evidence for a Gate.

    Callers must provide only Evidence applicable to the current Artifact
    versions. Multiple distinct statuses for one requirement fail closed.
    """
    requirement_ids = [item.requirement_id for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        raise ValueError("gate requirement ids must be unique")

    evidence_by_requirement: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        evidence_by_requirement[item.requirement_id].append(item)

    evaluations: list[RequirementEvaluation] = []
    blockers: list[GateRequirement] = []
    warnings: list[GateRequirement] = []

    for requirement in sorted(requirements, key=lambda item: (item.priority, item.requirement_id)):
        matching = [
            item
            for item in evidence_by_requirement.get(requirement.requirement_id, [])
            if item.repository_id == requirement.repository_id
            and item.ref == requirement.ref
            and item.commit_sha == requirement.commit_sha
            and item.kind == requirement.evidence_kind
        ]
        statuses = {item.status for item in matching}

        if not matching:
            status = EvidenceStatus.MISSING
        elif len(statuses) == 1:
            status = next(iter(statuses))
        else:
            status = min(statuses, key=_FAIL_CLOSED_PRECEDENCE.__getitem__)

        satisfied = status == EvidenceStatus.PASSED
        evaluations.append(
            RequirementEvaluation(
                requirement_id=requirement.requirement_id,
                status=status,
                evidence_ids=sorted(item.evidence_id for item in matching),
                satisfied=satisfied,
            )
        )
        if not satisfied:
            if requirement.severity == RequirementSeverity.BLOCKER:
                blockers.append(requirement)
            else:
                warnings.append(requirement)

    blockers.sort(key=lambda item: (item.priority, item.requirement_id))
    warnings.sort(key=lambda item: (item.priority, item.requirement_id))
    return GateEvaluation(
        decision=GateDecision.BLOCK if blockers else GateDecision.PASS,
        requirements=evaluations,
        blocker_requirement_ids=[item.requirement_id for item in blockers],
        warning_requirement_ids=[item.requirement_id for item in warnings],
        next_safe_action=blockers[0].failure_action if blockers else None,
    )
