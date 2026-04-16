"""Lazy-initialized council singleton."""
from __future__ import annotations

from pathlib import Path

import yaml

from agora.agents.agent import Agent
from agora.agents.council import Council
from agora.config.settings import get_config
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore
from agora.models.registry import get_registry
from agora.skills.store import SkillStore
from agora.tools.registry import ToolRegistry

_council: Council | None = None

USER_PROFILE_PATH: Path | None = None


def _get_data_dir() -> Path:
    d = Path(get_config().get("memory", {}).get("data_dir", "./data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_user_profile() -> str:
    global USER_PROFILE_PATH
    USER_PROFILE_PATH = _get_data_dir() / "user_profile.yaml"
    if not USER_PROFILE_PATH.exists():
        return ""
    data = yaml.safe_load(USER_PROFILE_PATH.read_text()) or {}
    return "\n".join(f"{k}: {v}" for k, v in data.items() if v)


def save_user_profile(key: str, value: str):
    path = _get_data_dir() / "user_profile.yaml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    data[key] = value
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))


def get_council() -> Council:
    global _council
    if _council is not None:
        return _council

    cfg = get_config()
    council_cfg = cfg.get("council", {})
    agent_cfgs = cfg.get("agents", {})
    model = council_cfg.get("model", "kiro")
    executor_model = council_cfg.get("executor_model", model)
    concurrent = council_cfg.get("concurrent", False)
    workspace = str(Path(council_cfg.get("workspace", "")).expanduser()) if council_cfg.get("workspace") else ""
    active = council_cfg.get("default_agents", ["scout", "architect", "critic"])

    agents = []
    for name in active:
        acfg = agent_cfgs.get(name, {})
        agent_model = acfg.get("model", model)  # per-agent model override
        agents.append(Agent(name=name, profile=acfg.get("profile", f"{name}.yaml"), model_name=agent_model))

    moderator = Agent(name="moderator", profile="moderator.yaml", model_name=model)
    synthesizer = Agent(name="synthesizer", profile="synthesizer.yaml", model_name=model)
    executor = Agent(name="executor", profile="executor.yaml", model_name=executor_model)

    # Get executor provider for tool-calling (may be API-based or CLI-based)
    registry = get_registry()
    try:
        executor_provider = registry.get(executor_model)
    except Exception:
        executor_provider = None

    # Setup sandbox if enabled
    sandbox = None
    from agora.sandbox.docker import get_sandbox_config
    sandbox_cfg = get_sandbox_config()
    if sandbox_cfg.enabled:
        from agora.sandbox.docker import DockerSandbox
        sandbox = DockerSandbox(sandbox_cfg)

    _council = Council(
        agents=agents,
        moderator=moderator,
        synthesizer=synthesizer,
        executor=executor,
        executor_provider=executor_provider,
        context=SharedContext(),
        memory=MemoryStore(),
        skill_store=SkillStore(),
        tool_registry=ToolRegistry(sandbox=sandbox),
        user_profile=_load_user_profile(),
        concurrent=concurrent,
        workspace=workspace,
    )
    return _council


def reset_council():
    global _council
    _council = None
