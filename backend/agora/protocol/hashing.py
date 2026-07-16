"""Canonical JSON and SHA-256 helpers used by protocol objects."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

_HASH_VALIDATION_BYPASS = object()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return to_jsonable_python(value)


def canonical_json_bytes(
    value: Any,
    *,
    exclude_top_level: frozenset[str] = frozenset(),
) -> bytes:
    """Serialize JSON deterministically for hashes and byte-level fixtures."""
    payload = _jsonable(value)
    if exclude_top_level:
        if not isinstance(payload, Mapping):
            raise TypeError("top-level exclusions require a mapping payload")
        payload = {key: item for key, item in payload.items() if key not in exclude_top_level}
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(
    value: Any,
    *,
    exclude_top_level: frozenset[str] = frozenset(),
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value, exclude_top_level=exclude_top_level)
    ).hexdigest()


def seal_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with a canonical top-level content hash."""
    sealed = dict(payload)
    sealed["content_sha256"] = canonical_sha256(
        sealed,
        exclude_top_level=frozenset({"content_sha256"}),
    )
    return sealed


SealedModel = TypeVar("SealedModel", bound=BaseModel)


def seal_model_payload(
    model: type[SealedModel],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize through a protocol model before calculating its content hash."""
    draft = dict(payload)
    draft["content_sha256"] = "0" * 64
    normalized = model.model_validate(
        draft,
        context={"hash_validation_token": _HASH_VALIDATION_BYPASS},
    ).model_dump(mode="json")
    return seal_payload(normalized)


def native_snapshot_id(identity: Mapping[str, Any]) -> str:
    return f"snapshot_{canonical_sha256(identity)[:32]}"
