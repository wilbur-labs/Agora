from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agora.orchestration import cli as orchestration_cli
from agora.orchestration.runtime import RuntimeCommand, build_runtime_registry
from agora.orchestration.runtime_capabilities import (
    RuntimeCapabilityProbe,
    collect_native_runtime_capabilities,
)
from agora.orchestration.processes import ProcessState, inspect_process
from agora.protocol.hashing import canonical_sha256
from agora.protocol.models import NativeRuntimeCapabilityObservation


COLLECTED_AT = datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc)


def _exact_probe(version: str) -> RuntimeCapabilityProbe:
    return RuntimeCapabilityProbe(
        installation_status="installed",
        version=version,
        version_status="exact",
        version_method="native_version_command",
        resolved_runtime_command_sha256=canonical_sha256(["resolved-runtime"]),
        resolved_version_command_sha256=canonical_sha256(["resolved-version"]),
        version_output_sha256=canonical_sha256(version),
        version_probe_exit_code=0,
    )


@pytest.mark.asyncio
async def test_capability_observation_is_sealed_ordered_and_non_authoritative():
    runtimes = build_runtime_registry({
        "orchestration": {
            "runtimes": {
                "claude": {
                    "declared_models": ["claude-sonnet-4-20250514"],
                },
            },
        },
    })

    async def probe(runtime: RuntimeCommand, platform: str) -> RuntimeCapabilityProbe:
        assert platform == "win32"
        return _exact_probe({
            "codex": "codex-cli 1.2.3",
            "claude": "2.1.217 (Claude Code)",
            "kiro": "kiro-cli-chat 2.13.1",
        }[runtime.adapter])

    observation = await collect_native_runtime_capabilities(
        runtimes,
        collected_at=COLLECTED_AT,
        platform="win32",
        probe=probe,
    )

    assert [item.adapter for item in observation.adapters] == [
        "claude",
        "codex",
        "kiro",
    ]
    assert observation.routing_authority is False
    assert observation.capability_declaration_id == "agora-foundation-routing-policy"
    claude = observation.adapters[0]
    assert claude.model_availability == "declared"
    assert claude.declared_models == ["claude-sonnet-4-20250514"]
    assert claude.declared_capabilities == [
        "correctness_review",
        "regression_review",
        "safety_review",
    ]
    codex = observation.adapters[1]
    assert codex.model_availability == "unavailable"
    assert codex.declared_models == []
    assert NativeRuntimeCapabilityObservation.model_validate(
        observation.model_dump(mode="json")
    ) == observation

    tampered = observation.model_dump(mode="json")
    tampered["adapters"][0]["version"] = "forged"
    with pytest.raises(ValidationError, match="content_sha256"):
        NativeRuntimeCapabilityObservation.model_validate(tampered)


def test_runtime_registry_bounds_version_and_model_declarations():
    defaults = build_runtime_registry({})
    assert defaults["codex"].version_command == ("codex", "--version")
    assert defaults["claude"].version_command == ("claude", "--version")
    assert defaults["kiro"].version_command == ("kiro-cli", "--version")

    custom = build_runtime_registry({
        "orchestration": {
            "runtimes": {
                "codex": {
                    "command": ["custom-codex", "{prompt}"],
                    "declared_models": ["gpt-5.2-codex", "gpt-5.1-codex"],
                },
            },
        },
    })
    assert custom["codex"].version_command is None
    assert custom["codex"].declared_models == ("gpt-5.1-codex", "gpt-5.2-codex")

    with pytest.raises(ValueError, match="without"):
        build_runtime_registry({
            "orchestration": {
                "runtimes": {
                    "codex": {"version_command": ["codex", "{prompt}"]},
                },
            },
        })
    with pytest.raises(ValueError, match="unique model identifiers"):
        build_runtime_registry({
            "orchestration": {
                "runtimes": {
                    "codex": {"declared_models": ["gpt-5", "gpt-5"]},
                },
            },
        })


@pytest.mark.asyncio
async def test_native_version_probe_observes_executable_without_provider_query():
    runtime = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "{prompt}"),
        version_command=(sys.executable, "--version"),
        declared_models=("test-model",),
    )

    observation = await collect_native_runtime_capabilities(
        {"codex": runtime},
        collected_at=COLLECTED_AT,
    )

    adapter = observation.adapters[0]
    assert adapter.installation_status == "installed"
    assert adapter.version_status == "exact"
    assert adapter.version_method == "native_version_command"
    assert adapter.version
    assert adapter.version_probe_exit_code == 0
    assert adapter.version_output_sha256 is not None


@pytest.mark.asyncio
async def test_oversized_native_version_output_fails_closed():
    runtime = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "{prompt}"),
        version_command=(
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('v' * 9000)",
        ),
    )

    observation = await collect_native_runtime_capabilities(
        {"codex": runtime},
        collected_at=COLLECTED_AT,
    )

    adapter = observation.adapters[0]
    assert adapter.installation_status == "installed"
    assert adapter.version_status == "unavailable"
    assert adapter.version_method == "probe_failed"
    assert adapter.version is None
    assert adapter.version_output_sha256 is not None


@pytest.mark.asyncio
async def test_version_probe_timeout_stops_and_reaps_real_child(
    monkeypatch,
    tmp_path,
):
    from agora.orchestration import runtime_capabilities

    pid_path = tmp_path / "timeout-probe.pid"
    monkeypatch.setattr(
        runtime_capabilities,
        "VERSION_PROBE_TIMEOUT_SECONDS",
        0.5,
    )
    runtime = _slow_python_runtime(pid_path)

    observation = await collect_native_runtime_capabilities(
        {"codex": runtime},
        collected_at=COLLECTED_AT,
    )

    adapter = observation.adapters[0]
    assert adapter.version_status == "unavailable"
    assert adapter.version_method == "probe_failed"
    assert adapter.version_probe_timed_out is True
    pid = int(pid_path.read_text(encoding="utf-8"))
    assert inspect_process(pid) == ProcessState.DEAD


@pytest.mark.asyncio
async def test_version_probe_cancellation_stops_child_and_propagates(tmp_path):
    pid_path = tmp_path / "cancelled-probe.pid"
    pending = asyncio.create_task(
        collect_native_runtime_capabilities(
            {"codex": _slow_python_runtime(pid_path)},
            collected_at=COLLECTED_AT,
        )
    )
    for _ in range(200):
        if pid_path.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_path.exists()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    pid = int(pid_path.read_text(encoding="utf-8"))
    assert inspect_process(pid) == ProcessState.DEAD


@pytest.mark.asyncio
async def test_uninspectable_wrapper_and_unconfigured_version_are_explicit(
    monkeypatch,
    tmp_path,
):
    from agora.orchestration import runtime_capabilities

    wrapper = tmp_path / "custom.cmd"
    wrapper.write_text("@echo off\r\necho unsupported\r\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_capabilities.shutil,
        "which",
        lambda _: str(wrapper),
    )
    uninspectable = await collect_native_runtime_capabilities(
        {
            "codex": RuntimeCommand(
                adapter="codex",
                command_template=("custom", "{prompt}"),
            ),
        },
        collected_at=COLLECTED_AT,
        platform="win32",
    )
    assert uninspectable.adapters[0].installation_status == "uninspectable"
    assert uninspectable.adapters[0].version_method == "probe_failed"

    monkeypatch.undo()
    not_configured = await collect_native_runtime_capabilities(
        {
            "codex": RuntimeCommand(
                adapter="codex",
                command_template=(sys.executable, "{prompt}"),
            ),
        },
        collected_at=COLLECTED_AT,
    )
    assert not_configured.adapters[0].installation_status == "installed"
    assert not_configured.adapters[0].version_status == "unavailable"
    assert not_configured.adapters[0].version_method == "not_configured"


@pytest.mark.asyncio
async def test_version_probe_scrubs_proxy_environment(monkeypatch):
    from agora.orchestration import runtime_capabilities

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    captured_environment = {}

    class FakeProcess:
        returncode = 0
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()

        def __init__(self):
            self.stdout.feed_data(b"codex 1.0\n")
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        async def wait(self):
            return 0

    async def create_process(*command, **kwargs):
        captured_environment.update(kwargs["env"])
        return FakeProcess()

    monkeypatch.setattr(
        runtime_capabilities.asyncio,
        "create_subprocess_exec",
        create_process,
    )
    observation = await collect_native_runtime_capabilities(
        {
            "codex": RuntimeCommand(
                adapter="codex",
                command_template=(sys.executable, "{prompt}"),
                version_command=(sys.executable, "--version"),
            ),
        },
        collected_at=COLLECTED_AT,
    )

    assert observation.adapters[0].version_status == "exact"
    assert not any(
        name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        for name in captured_environment
    )


@pytest.mark.asyncio
async def test_probe_failure_cancels_and_awaits_sibling_observations():
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def probe(runtime: RuntimeCommand, platform: str) -> RuntimeCapabilityProbe:
        if runtime.adapter == "claude":
            sibling_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
        await sibling_started.wait()
        raise OSError("simulated executable lookup failure")

    runtimes = {
        name: RuntimeCommand(
            adapter=name,
            command_template=(sys.executable, "{prompt}"),
        )
        for name in ("claude", "codex")
    }
    with pytest.raises(OSError, match="simulated"):
        await collect_native_runtime_capabilities(
            runtimes,
            collected_at=COLLECTED_AT,
            probe=probe,
        )
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_executable_lookup_error_is_uninspectable(monkeypatch):
    from agora.orchestration import runtime_capabilities

    def fail_lookup(_command):
        raise OSError("simulated PATH failure")

    monkeypatch.setattr(runtime_capabilities.shutil, "which", fail_lookup)
    observation = await collect_native_runtime_capabilities(
        {
            "codex": RuntimeCommand(
                adapter="codex",
                command_template=("codex", "{prompt}"),
            ),
        },
        collected_at=COLLECTED_AT,
    )

    assert observation.adapters[0].installation_status == "uninspectable"
    assert observation.adapters[0].version_status == "unavailable"
    assert observation.adapters[0].version_method == "probe_failed"


@pytest.mark.asyncio
async def test_missing_runtime_is_truthfully_unavailable(monkeypatch):
    from agora.orchestration import runtime_capabilities

    monkeypatch.setattr(runtime_capabilities.shutil, "which", lambda _: None)
    runtime = RuntimeCommand(
        adapter="codex",
        command_template=("definitely-missing-runtime", "{prompt}"),
        version_command=("definitely-missing-runtime", "--version"),
    )

    observation = await collect_native_runtime_capabilities(
        {"codex": runtime},
        collected_at=COLLECTED_AT,
    )

    adapter = observation.adapters[0]
    assert adapter.installation_status == "not_found"
    assert adapter.version_status == "unavailable"
    assert adapter.version_method == "not_installed"
    assert adapter.version is None
    assert adapter.resolved_runtime_command_sha256 is None


def test_capabilities_cli_does_not_build_task_service_or_database(
    monkeypatch,
    capsys,
):
    runtimes = build_runtime_registry({})

    async def probe(runtime: RuntimeCommand, platform: str) -> RuntimeCapabilityProbe:
        return _exact_probe(f"{runtime.adapter} 1.0")

    observation = asyncio.run(
        collect_native_runtime_capabilities(
            runtimes,
            collected_at=COLLECTED_AT,
            platform="win32",
            probe=probe,
        )
    )

    async def collect(_runtimes):
        return observation

    monkeypatch.setattr(orchestration_cli, "get_config", lambda: {})
    monkeypatch.setattr(
        orchestration_cli,
        "collect_native_runtime_capabilities",
        collect,
    )
    monkeypatch.setattr(
        orchestration_cli,
        "build_service",
        lambda: pytest.fail("capabilities must not initialize Task storage"),
    )

    assert orchestration_cli.main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1.0"
    assert payload["routing_authority"] is False


def _slow_python_runtime(pid_path: Path) -> RuntimeCommand:
    script = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(60)"
    )
    return RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "{prompt}"),
        version_command=(sys.executable, "-c", script),
    )
