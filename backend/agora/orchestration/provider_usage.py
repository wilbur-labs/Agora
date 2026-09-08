"""Pure normalization of native CLI output into Run-bound usage observations."""
from __future__ import annotations

import json
import math
import re
from enum import Enum
from typing import Any

from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.models import ProviderUsageObservation


class RuntimeResultFormat(str, Enum):
    PLAIN_TEXT = "plain_text"
    CODEX_JSONL_V1 = "codex_jsonl_v1"
    CLAUDE_JSON_V1 = "claude_json_v1"
    KIRO_CHAT_V1 = "kiro_chat_v1"
    #: ``claude -p --output-format stream-json``. The event stream ends with a
    #: ``result`` event carrying the same fields as the single-envelope ``json``
    #: format, so the two share one reader.
    CLAUDE_STREAM_JSON_V1 = "claude_stream_json_v1"
    #: ``kiro-cli chat --agent-engine v2 --output-format stream-json``: ACP events.
    #: Reports credits rather than token counts, so tokens read *unavailable*
    #: here where KIRO_CHAT_V1 estimated them from byte length — a real loss of a
    #: made-up number in exchange for a measured one.
    KIRO_STREAM_JSON_V1 = "kiro_stream_json_v1"


_KIRO_ASSISTANT_MARKER = "\x1b[38;5;141m> \x1b[0m"
_KIRO_TRAILER_MARKER = "\x1b[38;5;252m"
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_native_output(
    *,
    adapter: str,
    result_format: RuntimeResultFormat,
    stdout: str,
    run_id: str,
    stdout_prefix_truncated: bool = False,
    prompt: str | None = None,
) -> tuple[str, ProviderUsageObservation | None]:
    """Return semantic stdout and provider facts without mutating native state."""

    if result_format == RuntimeResultFormat.CODEX_JSONL_V1:
        return _normalize_codex_jsonl(
            stdout,
            run_id=run_id,
            adapter=adapter,
            stdout_prefix_truncated=stdout_prefix_truncated,
        )
    if result_format == RuntimeResultFormat.CLAUDE_JSON_V1:
        return _normalize_claude_json(stdout, run_id=run_id, adapter=adapter)
    if result_format == RuntimeResultFormat.CLAUDE_STREAM_JSON_V1:
        return _normalize_claude_stream_json(
            stdout, run_id=run_id, adapter=adapter,
            stdout_prefix_truncated=stdout_prefix_truncated,
        )
    if result_format == RuntimeResultFormat.KIRO_STREAM_JSON_V1:
        return _normalize_kiro_stream_json(
            stdout, run_id=run_id, adapter=adapter,
            stdout_prefix_truncated=stdout_prefix_truncated,
        )
    if result_format == RuntimeResultFormat.KIRO_CHAT_V1:
        return _normalize_kiro_chat(
            stdout,
            run_id=run_id,
            adapter=adapter,
            prompt=prompt,
        )
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
    if exit_code is None or result_format not in {
        RuntimeResultFormat.PLAIN_TEXT,
        RuntimeResultFormat.KIRO_CHAT_V1,
    }:
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
    stdout_prefix_truncated: bool,
) -> tuple[str, ProviderUsageObservation | None]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    events: list[Any] = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # The bounded Runner retains a byte tail. If it overflowed, only
            # the first retained JSONL event may be a partial transport frame.
            # Discarding that one prefix is format-only recovery: every
            # subsequent event must still be valid and the final semantic
            # message plus usage event remain mandatory below.
            if index == 0 and stdout_prefix_truncated:
                continue
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


def _jsonl_events(stdout: str, *, prefix_truncated: bool = False) -> list[dict[str, Any]] | None:
    """Every JSON object in a JSON Lines stream, or None if a line is not one.

    Shared by the two stream-json readers below. All-or-nothing on purpose: a
    stream with an unparseable line is one whose shape this reader does not
    actually know, and half-reading it would produce usage figures attributed to
    a turn nobody verified.

    ``prefix_truncated`` is the one exception, and it is format-only recovery of
    exactly one line: the run's retained output is a byte *tail*, so when it
    overflowed, its first line is the back half of an event. Discarding that one
    fragment is what keeps a long run — the kind most worth measuring — from
    losing its usage figures over a line that was never whole. Every later line
    must still parse, and the closing event this reader needs is at the end.
    """
    events: list[dict[str, Any]] = []
    for index, line in enumerate(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            if index == 0 and prefix_truncated:
                continue
            return None
        if not isinstance(event, dict):
            if index == 0 and prefix_truncated:
                continue
            return None
        events.append(event)
    return events or None


def _normalize_claude_stream_json(
    stdout: str,
    *,
    run_id: str,
    adapter: str,
    stdout_prefix_truncated: bool = False,
) -> tuple[str, ProviderUsageObservation | None]:
    """``claude -p --output-format stream-json``: read the closing result event.

    The final ``{"type": "result", ...}`` event carries ``result``, ``usage``,
    ``total_cost_usd``, ``modelUsage`` and ``duration_ms`` — the same fields, with
    the same meanings, as the whole envelope that ``--output-format json`` prints.
    So it is handed to the same reader instead of a second copy of that logic,
    and the two formats cannot drift into disagreeing about the same numbers.
    """
    events = _jsonl_events(stdout, prefix_truncated=stdout_prefix_truncated)
    if events is None:
        return stdout, None
    results = [event for event in events if event.get("type") == "result"]
    if len(results) != 1:
        # None: the run died before its closing event, so there are no usage
        # facts to report — not zero usage. More than one: this is not the single
        # turn this reader knows how to attribute.
        return stdout, None
    semantic, observation = _claude_envelope(
        results[0], run_id=run_id, adapter=adapter, source="claude_stream_json",
    )
    if observation is None:
        # Envelope present but unreadable; the streamed text is still the better
        # answer than a page of JSON, when the event carried one.
        return (semantic if semantic else stdout), None
    return semantic, observation


def _normalize_kiro_stream_json(
    stdout: str,
    *,
    run_id: str,
    adapter: str,
    stdout_prefix_truncated: bool = False,
) -> tuple[str, ProviderUsageObservation | None]:
    """``kiro-cli chat --agent-engine v2 --output-format stream-json``: ACP events.

    Verified against kiro-cli 2.19.1 on 2026-09-08. Two things are taken from the
    stream and one is deliberately refused:

    - ``runFinished.finalText`` is the turn's answer, and
      ``runFinished.finalTextTruncated`` says whether kiro itself cut it short. A
      truncated answer is recorded **with that fact appended**, because a clipped
      answer presented as a whole one is the product misreporting its own read.
    - ``metadata.meteringUsage`` reports credits, which the observation records as
      native credits — a measured provider figure.
    - **Tokens are not estimated.** The plain-text reader divided byte length by
      four; that number was always a guess, and having a real credit figure is no
      reason to keep manufacturing a fake token one.
    """
    events = _jsonl_events(stdout, prefix_truncated=stdout_prefix_truncated)
    if events is None:
        return stdout, None
    finished = [event for event in events if event.get("type") == "runFinished"]
    if len(finished) != 1:
        return stdout, None
    data = finished[0].get("data")
    if not isinstance(data, dict) or not isinstance(data.get("finalText"), str):
        return stdout, None
    semantic = data["finalText"]
    if data.get("finalTextTruncated") is True:
        semantic = f"{semantic}\n\n[kiro 声明这段最终文本被它自己截断了,显示的不是全文]"

    credits = _kiro_credits(events)
    duration_ms = None
    for event in reversed(events):
        if event.get("type") == "metadata" and isinstance(event.get("data"), dict):
            duration_ms = _nonnegative_int(event["data"].get("turnDurationMs"))
            if duration_ms is not None:
                break
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "adapter": adapter,
        "provider": _provider(adapter),
        "source": "kiro_acp_stream",
        "source_payload_sha256": canonical_sha256({"credits": credits, "duration_ms": duration_ms}),
        "duration_ms": duration_ms,
        "token_measurement": "unavailable",
        "token_method": "unavailable",
        "cost_measurement": "unavailable",
        "cost_method": "unavailable",
    }
    if credits is not None:
        payload.update({
            "native_credits": credits,
            "native_credit_measurement": "exact",
            "native_credit_method": "provider_reported_native_credits",
        })
    return semantic, _sealed(payload)


def _kiro_credits(events: list[dict[str, Any]]) -> float | None:
    """Credits from the last metering report, or None if none was reported.

    The last one, not the sum of all: kiro repeats a running metering block on
    successive ``metadata`` events within one turn, so adding them up would
    multiply the turn's cost by however many times it reported.
    """
    for event in reversed(events):
        if event.get("type") != "metadata":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        metering = data.get("meteringUsage")
        if not isinstance(metering, list) or not metering:
            continue
        total = 0.0
        seen = False
        for entry in metering:
            if not isinstance(entry, dict) or entry.get("unit") != "credit":
                continue
            value = entry.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                total += float(value)
                seen = True
        if seen:
            return total
    return None


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
    if not isinstance(envelope, dict):
        return stdout, None
    semantic, observation = _claude_envelope(envelope, run_id=run_id, adapter=adapter)
    return (semantic if semantic else stdout), observation


def _claude_envelope(
    envelope: dict[str, Any],
    *,
    run_id: str,
    adapter: str,
    source: str = "claude_print_json",
) -> tuple[str, ProviderUsageObservation | None]:
    """Usage facts from one claude result envelope, however it was delivered."""
    if not isinstance(envelope.get("result"), str):
        return "", None
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
        "source": source,
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


def _normalize_kiro_chat(
    stdout: str,
    *,
    run_id: str,
    adapter: str,
    prompt: str | None,
) -> tuple[str, ProviderUsageObservation | None]:
    """Extract only the final explicitly marked assistant turn from Kiro chat UI."""

    if _KIRO_ASSISTANT_MARKER not in stdout:
        return stdout, None
    observation = None
    if prompt is not None:
        estimated = max(
            1,
            math.ceil(len((prompt + stdout).encode("utf-8")) / 4),
        )
        observation = _sealed({
            "schema_version": "1.0",
            "run_id": run_id,
            "adapter": adapter,
            "provider": _provider(adapter),
            "source": "kiro_cli_text",
            "total_tokens": estimated,
            "token_measurement": "estimated",
            "token_method": "utf8_bytes_divided_by_four_ceil",
            "cost_measurement": "unavailable",
            "cost_method": "unavailable",
        })
    final_turn = stdout.rsplit(_KIRO_ASSISTANT_MARKER, 1)[1]
    if _KIRO_TRAILER_MARKER in final_turn:
        final_turn = final_turn.split(_KIRO_TRAILER_MARKER, 1)[0]
    final_turn = _ANSI_ESCAPE_RE.sub("", final_turn)
    final_turn = _CONTROL_RE.sub("", final_turn)
    lines: list[str] = []
    for line in final_turn.splitlines():
        if line.strip().startswith("▸ Credits:"):
            break
        lines.append(line)
    semantic = "\n".join(lines).strip()
    return (semantic, observation) if semantic else (stdout, None)


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
