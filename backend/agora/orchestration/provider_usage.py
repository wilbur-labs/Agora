"""Pure normalization of native CLI output into Run-bound usage observations."""
from __future__ import annotations

import json
import math
from enum import Enum
from typing import Any

from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.models import ProviderUsageObservation


class RuntimeResultFormat(str, Enum):
    PLAIN_TEXT = "plain_text"
    CODEX_JSONL_V1 = "codex_jsonl_v1"
    CLAUDE_JSON_V1 = "claude_json_v1"


def normalize_native_output(
    *,
    adapter: str,
    result_format: RuntimeResultFormat,
    stdout: str,
    run_id: str,
) -> tuple[str, ProviderUsageObservation | None]:
    """Return semantic stdout and provider facts without mutating native state."""

    if result_format == RuntimeResultFormat.CODEX_JSONL_V1:
        return _normalize_codex_jsonl(stdout, run_id=run_id, adapter=adapter)
    if result_format == RuntimeResultFormat.CLAUDE_JSON_V1:
        return _normalize_claude_json(stdout, run_id=run_id, adapter=adapter)
    return stdout, None


def settlement_observation(
    *,
    run_id: str,
    adapter: str,
    prompt: str,
    output: str,
    process_started: bool,
    exit_code: int | None,
    result_format: RuntimeResultFormat,
    native_observation: ProviderUsageObservation | None,
) -> ProviderUsageObservation:
    """Choose provider facts or an explicit fallback for one terminal Run."""

    if native_observation is not None:
        if (
            native_observation.run_id == run_id
            and native_observation.adapter == adapter
        ):
            return native_observation
        return _unavailable(run_id, adapter)
    if not process_started:
        return _sealed({
            "schema_version": "1.0",
            "run_id": run_id,
            "adapter": adapter,
            "provider": _provider(adapter),
            "source": "process_not_started",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
            "token_measurement": "exact",
            "token_method": "process_not_started",
            "cost_usd": 0.0,
            "cost_measurement": "exact",
            "cost_method": "process_not_started",
            "native_credits": 0.0,
            "native_credit_measurement": "exact",
            "native_credit_method": "process_not_started",
        })
    if exit_code is None or result_format != RuntimeResultFormat.PLAIN_TEXT:
        return _unavailable(run_id, adapter)

    estimated = max(1, math.ceil(len((prompt + output).encode("utf-8")) / 4))
    source = "kiro_cli_text" if adapter == "kiro" else "custom_text"
    return _sealed({
        "schema_version": "1.0",
        "run_id": run_id,
        "adapter": adapter,
        "provider": _provider(adapter),
        "source": source,
        "total_tokens": estimated,
        "token_measurement": "estimated",
        "token_method": "utf8_bytes_divided_by_four_ceil",
        "cost_measurement": "unavailable",
        "cost_method": "unavailable",
    })


def _normalize_codex_jsonl(
    stdout: str,
    *,
    run_id: str,
    adapter: str,
) -> tuple[str, ProviderUsageObservation | None]:
    try:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return stdout, None
    if not events or any(not isinstance(event, dict) for event in events):
        return stdout, None

    messages = [
        event.get("item", {}).get("text")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
        and isinstance(event["item"].get("text"), str)
    ]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if len(completed) != 1 or not messages:
        return stdout, None
    usage = completed[0].get("usage")
    if not isinstance(usage, dict):
        return messages[-1], None

    input_tokens = _nonnegative_int(usage.get("input_tokens"))
    output_tokens = _nonnegative_int(usage.get("output_tokens"))
    cache_read = _optional_nonnegative_int(usage, "cached_input_tokens")
    reasoning = _optional_nonnegative_int(usage, "reasoning_output_tokens")
    if input_tokens is None or output_tokens is None:
        return messages[-1], None
    facts = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cache_read,
        "reasoning_output_tokens": reasoning,
    }
    observation = _sealed({
        "schema_version": "1.0",
        "run_id": run_id,
        "adapter": adapter,
        "provider": "openai",
        "source": "codex_exec_jsonl",
        "source_payload_sha256": canonical_sha256(facts),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "reasoning_output_tokens": reasoning,
        # Codex reports cached input as a subset of input and reasoning output as
        # a subset of output, so neither detail field is added twice.
        "total_tokens": input_tokens + output_tokens,
        "token_measurement": "exact",
        "token_method": "provider_input_plus_output",
        "cost_measurement": "unavailable",
        "cost_method": "unavailable",
    })
    return messages[-1], observation


def _normalize_claude_json(
    stdout: str,
    *,
    run_id: str,
    adapter: str,
) -> tuple[str, ProviderUsageObservation | None]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), str):
        return stdout, None
    result = envelope["result"]
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return result, None

    input_tokens = _nonnegative_int(usage.get("input_tokens"))
    output_tokens = _nonnegative_int(usage.get("output_tokens"))
    cache_read = _nonnegative_int(usage.get("cache_read_input_tokens"))
    cache_creation = _nonnegative_int(usage.get("cache_creation_input_tokens"))
    exact_tokens = all(
        item is not None
        for item in (input_tokens, output_tokens, cache_read, cache_creation)
    )
    cost = _nonnegative_float(envelope.get("total_cost_usd"))
    duration_ms = _nonnegative_int(envelope.get("duration_ms"))
    model_usage = envelope.get("modelUsage")
    model = None
    if isinstance(model_usage, dict) and len(model_usage) == 1:
        candidate = next(iter(model_usage))
        if isinstance(candidate, str) and 0 < len(candidate) <= 200:
            model = candidate

    facts = {
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
        "total_cost_usd": cost,
        "duration_ms": duration_ms,
        "model": model,
    }
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "adapter": adapter,
        "provider": "anthropic",
        "source": "claude_print_json",
        "source_payload_sha256": canonical_sha256(facts),
        "model": model,
        "duration_ms": duration_ms,
        "cost_usd": cost,
        "cost_measurement": "exact" if cost is not None else "unavailable",
        "cost_method": (
            "provider_reported_total_cost_usd" if cost is not None else "unavailable"
        ),
    }
    if exact_tokens:
        assert input_tokens is not None and output_tokens is not None
        assert cache_read is not None and cache_creation is not None
        payload.update({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "total_tokens": input_tokens + output_tokens + cache_read + cache_creation,
            "token_measurement": "exact",
            "token_method": "provider_input_output_and_cache",
        })
    else:
        payload.update({
            "token_measurement": "unavailable",
            "token_method": "unavailable",
        })
    return result, _sealed(payload)


def _unavailable(run_id: str, adapter: str) -> ProviderUsageObservation:
    return _sealed({
        "schema_version": "1.0",
        "run_id": run_id,
        "adapter": adapter,
        "provider": _provider(adapter),
        "source": "runtime_boundary",
        "token_measurement": "unavailable",
        "token_method": "unavailable",
        "cost_measurement": "unavailable",
        "cost_method": "unavailable",
    })


def _provider(adapter: str) -> str:
    return {"codex": "openai", "claude": "anthropic", "kiro": "kiro"}.get(
        adapter,
        "unknown",
    )


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_nonnegative_int(values: dict[str, Any], key: str) -> int | None:
    return _nonnegative_int(values[key]) if key in values else None


def _nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) and converted >= 0 else None


def _sealed(payload: dict[str, Any]) -> ProviderUsageObservation:
    return ProviderUsageObservation.model_validate(
        seal_model_payload(ProviderUsageObservation, payload)
    )
