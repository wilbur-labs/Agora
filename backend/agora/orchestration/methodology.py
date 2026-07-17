"""Versioned provisional method used until the authoritative AI-DLC graph is restored."""
from __future__ import annotations

import hashlib
import json

from .models import MethodologyDefinition, StageDefinition


FOUNDATION_METHODOLOGY = MethodologyDefinition(
    methodology_id="agora-aidlc-foundation",
    version="0.1",
    provisional=True,
    description=(
        "A minimal read-only planning and review loop. It is not the authoritative "
        "AI-DLC phase graph and must not be presented as complete delivery."
    ),
    stages=[
        StageDefinition(
            stage_key="solution_design",
            title="Engineering solution design",
            role="engineering_planner",
            adapter="codex",
            token_weight=45,
            objective=(
                "Produce a concrete implementation plan, affected areas, risks, verification "
                "strategy, and explicit unknowns without modifying repository files."
            ),
        ),
        StageDefinition(
            stage_key="correctness_review",
            title="Independent correctness and safety review",
            role="independent_reviewer",
            adapter="claude",
            token_weight=30,
            objective=(
                "Independently review the proposed solution for correctness, safety, regression "
                "coverage, missing requirements, and unjustified assumptions."
            ),
        ),
        StageDefinition(
            stage_key="methodology_review",
            title="Methodology and quality-gate review",
            role="methodology_steward",
            adapter="kiro",
            token_weight=25,
            objective=(
                "Check that the plan has explicit lifecycle boundaries, artifacts, evidence, "
                "quality gates, rework paths, approvals, and a safe next action."
            ),
        ),
    ],
)


def methodology_sha256(methodology: MethodologyDefinition) -> str:
    payload = json.dumps(
        methodology.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
