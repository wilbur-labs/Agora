"""Registry and deterministic JSON Schema export for protocol contracts."""
from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel

from .models import (
    Approval,
    Artifact,
    ConsultationCandidate,
    ConsultationCandidateDisposition,
    ContextPack,
    Evidence,
    GateRequirement,
    HandoffPack,
    NativeRuntimeCapabilityObservation,
    NativeStateSnapshot,
    PinnedRuntimePreflightDecision,
    ProviderUsageObservation,
    RunProtocolState,
    RunnerIsolationContract,
    StageInventory,
)

SchemaModel: TypeAlias = type[BaseModel]

SCHEMA_MODELS: dict[str, SchemaModel] = {
    "approval": Approval,
    "artifact": Artifact,
    "consultation-candidate": ConsultationCandidate,
    "consultation-candidate-disposition": ConsultationCandidateDisposition,
    "context-pack": ContextPack,
    "evidence": Evidence,
    "gate-requirement": GateRequirement,
    "handoff-pack": HandoffPack,
    "native-runtime-capability-observation": NativeRuntimeCapabilityObservation,
    "native-state-snapshot": NativeStateSnapshot,
    "pinned-runtime-preflight-decision": PinnedRuntimePreflightDecision,
    "provider-usage-observation": ProviderUsageObservation,
    "run-protocol-state": RunProtocolState,
    "runner-isolation-contract": RunnerIsolationContract,
    "stage-inventory": StageInventory,
}


def schema_document(name: str, model: SchemaModel) -> dict:
    generated = model.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://agora.local/schemas/v1/{name}.schema.json",
        **generated,
    }
