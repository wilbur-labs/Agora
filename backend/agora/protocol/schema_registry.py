"""Registry and deterministic JSON Schema export for protocol contracts."""
from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel

from .methodology import MethodologySourceGraph
from .methodology_activation import MethodologyActivationDefinition
from .methodology_execution import MethodologyExecutionContract
from .methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
)
from .methodology_route_activation import (
    MethodologyRouteActivationReceipt,
    MethodologyRouteActivationRequest,
)
from .methodology_run_claim import (
    MethodologyRunClaimReceipt,
    MethodologyRunClaimRequest,
)
from .methodology_run_dispatch import (
    MethodologyRunDispatchClaim,
    MethodologyRunDispatchPolicyDecision,
    MethodologyRunDispatchReceipt,
)
from .models import (
    Approval,
    Artifact,
    ConsultationCandidate,
    ConsultationCandidateDraft,
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
    "authenticated-methodology-migration-gate": (
        AuthenticatedMethodologyMigrationGate
    ),
    "consultation-candidate": ConsultationCandidate,
    "consultation-candidate-draft": ConsultationCandidateDraft,
    "consultation-candidate-disposition": ConsultationCandidateDisposition,
    "context-pack": ContextPack,
    "evidence": Evidence,
    "gate-requirement": GateRequirement,
    "handoff-pack": HandoffPack,
    "methodology-activation-definition": MethodologyActivationDefinition,
    "methodology-execution-contract": MethodologyExecutionContract,
    "methodology-migration-activation-receipt": (
        MethodologyMigrationActivationReceipt
    ),
    "methodology-migration-preview-decision": MethodologyMigrationPreviewDecision,
    "methodology-migration-preview-request": MethodologyMigrationPreviewRequest,
    "methodology-route-activation-receipt": MethodologyRouteActivationReceipt,
    "methodology-route-activation-request": MethodologyRouteActivationRequest,
    "methodology-run-claim-receipt": MethodologyRunClaimReceipt,
    "methodology-run-claim-request": MethodologyRunClaimRequest,
    "methodology-run-dispatch-claim": MethodologyRunDispatchClaim,
    "methodology-run-dispatch-policy-decision": (
        MethodologyRunDispatchPolicyDecision
    ),
    "methodology-run-dispatch-receipt": MethodologyRunDispatchReceipt,
    "methodology-source-graph": MethodologySourceGraph,
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
