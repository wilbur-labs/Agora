"""Lazy-initialized council singleton."""
from __future__ import annotations

from pathlib import Path

import yaml

from agora.agents.agent import Agent
from agora.agents.council import Council
from agora.config.settings import get_config
from agora.context.shared import SharedContext
from agora.memory.store import MemoryStore

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
    active = council_cfg.get("default_agents", ["scout", "architect", "critic"])

    agents = []
    for name in active:
        acfg = agent_cfgs.get(name, {})
        agents.append(Agent(name=name, profile=acfg.get("profile", f"{name}.yaml"), model_name=model))

    # Moderator — clarify-first agent
    moderator = Agent(name="moderator", profile="moderator.yaml", model_name=model)

    _council = Council(
        agents=agents,
        moderator=moderator,
        context=SharedContext(),
        memory=MemoryStore(),
        user_profile=_load_user_profile(),
    )
    return _council


def reset_council():
    global _council
    _council = None
