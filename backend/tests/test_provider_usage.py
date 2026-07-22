from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agora.orchestration.provider_usage import (
    RuntimeResultFormat,
    normalize_native_output,
    settlement_observation,
)
from agora.orchestration.runtime import build_runtime_registry
from agora.protocol.models import ProviderUsageObservation


def test_default_structured_formats_are_explicit_and_custom_commands_fail_safe():
    defaults = build_runtime_registry({})
    assert defaults["codex"].result_format == RuntimeResultFormat.CODEX_JSONL_V1
    assert "--json" in defaults["codex"].command_template
    assert defaults["claude"].result_format == RuntimeResultFormat.CLAUDE_JSON_V1
    assert ("--output-format", "json") in tuple(
        zip(
            defaults["claude"].command_template,
            defaults["claude"].command_template[1:],
        )
    )
    assert defaults["kiro"].result_format == RuntimeResultFormat.PLAIN_TEXT

    custom = build_runtime_registry({
        "orchestration": {
            "runtimes": {
                "codex": {"command": ["custom-codex", "{prompt}"]},
            },
        },
    })
    assert custom["codex"].result_format == RuntimeResultFormat.PLAIN_TEXT
    with pytest.raises(ValueError, match="does not match its adapter"):
        build_runtime_registry({
            "orchestration": {
                "runtimes": {
                    "kiro": {"result_format": "claude_json_v1"},
                },
            },
        })


def test_codex_jsonl_extracts_exact_usage_without_double_counting_cached_input():
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "thread_1"}),
        json.dumps({
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": "done"},
        }),
        json.dumps({
            "type": "turn.completed",
            "usage": {
                "input_tokens": 24_763,
                "cached_input_tokens": 24_448,
                "output_tokens": 122,
                "reasoning_output_tokens": 37,
            },
        }),
    ])

    output, observation = normalize_native_output(
        adapter="codex",
        result_format=RuntimeResultFormat.CODEX_JSONL_V1,
        stdout=stdout,
        run_id="orun_codex",
    )

    assert output == "done"
    assert observation is not None
    assert observation.total_tokens == 24_885
    assert observation.cache_read_input_tokens == 24_448
    assert observation.reasoning_output_tokens == 37
    assert observation.token_measurement == "exact"
    assert observation.cost_usd is None
    assert observation.cost_measurement == "unavailable"


def test_claude_json_extracts_exact_components_cost_model_and_duration():
    stdout = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "done",
        "duration_ms": 2_065,
        "total_cost_usd": 0.017915,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 1_748,
            "cache_read_input_tokens": 0,
            "output_tokens": 17,
        },
        "modelUsage": {"claude-opus-4-8[1m]": {"costUSD": 0.017915}},
    })

    output, observation = normalize_native_output(
        adapter="claude",
        result_format=RuntimeResultFormat.CLAUDE_JSON_V1,
        stdout=stdout,
        run_id="orun_claude",
    )

    assert output == "done"
    assert observation is not None
    assert observation.total_tokens == 1_767
    assert observation.token_measurement == "exact"
    assert observation.cost_usd == 0.017915
    assert observation.cost_measurement == "exact"
    assert observation.model == "claude-opus-4-8[1m]"
    assert observation.duration_ms == 2_065


def test_structured_parse_failure_is_unavailable_and_never_zero():
    output, native = normalize_native_output(
        adapter="codex",
        result_format=RuntimeResultFormat.CODEX_JSONL_V1,
        stdout="not-jsonl",
        run_id="orun_invalid",
    )
    observation = settlement_observation(
        run_id="orun_invalid",
        adapter="codex",
        prompt="prompt",
        output=output,
        process_started=True,
        exit_code=0,
        result_format=RuntimeResultFormat.CODEX_JSONL_V1,
        native_observation=native,
    )

    assert observation.total_tokens is None
    assert observation.token_measurement == "unavailable"
    assert observation.cost_usd is None
    assert observation.cost_measurement == "unavailable"


def test_plain_kiro_is_estimated_but_process_launch_failure_is_exact_zero():
    estimated = settlement_observation(
        run_id="orun_kiro",
        adapter="kiro",
        prompt="abcd",
        output="efgh",
        process_started=True,
        exit_code=0,
        result_format=RuntimeResultFormat.PLAIN_TEXT,
        native_observation=None,
    )
    assert estimated.total_tokens == 2
    assert estimated.token_measurement == "estimated"
    assert estimated.source == "kiro_cli_text"
    assert estimated.cost_usd is None

    launch_failed = settlement_observation(
        run_id="orun_launch",
        adapter="claude",
        prompt="ignored",
        output="",
        process_started=False,
        exit_code=None,
        result_format=RuntimeResultFormat.CLAUDE_JSON_V1,
        native_observation=None,
    )
    assert launch_failed.total_tokens == 0
    assert launch_failed.token_measurement == "exact"
    assert launch_failed.cost_usd == 0
    assert launch_failed.cost_measurement == "exact"


def test_observation_hash_and_run_binding_are_tamper_evident():
    _, observation = normalize_native_output(
        adapter="claude",
        result_format=RuntimeResultFormat.CLAUDE_JSON_V1,
        stdout=json.dumps({
            "result": "done",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            },
        }),
        run_id="orun_bound",
    )
    assert observation is not None
    tampered = observation.model_dump(mode="json")
    tampered["run_id"] = "orun_other"
    with pytest.raises(ValidationError, match="content_sha256"):
        ProviderUsageObservation.model_validate(tampered)
