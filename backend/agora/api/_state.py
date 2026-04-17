"""Session-aware council management — each session gets independent context."""
from __future__ import annotations

from collections import OrderedDict
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

_MAX_SESSIONS = 20  # LRU cache size

# Shared resources (created once)
_shared: dict | None = None

# Per-session councils
_sessions: OrderedDict[str, Council] = OrderedDict()

# Default session (for requests without session_id)
_DEFAULT_SESSION = "__default__"

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


def _init_shared() -> dict:
    """Initialize shared resources (agents, providers, etc.) once."""
    global _shared
    if _shared is not None:
        return _shared

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
        agent_model = acfg.get("model", model)
        agents.append(Agent(name=name, profile=acfg.get("profile", f"{name}.yaml"), model_name=agent_model))

    moderator = Agent(name="moderator", profile="moderator.yaml", model_name=model)
    synthesizer = Agent(name="synthesizer", profile="synthesizer.yaml", model_name=model)
    executor = Agent(name="executor", profile="executor.yaml", model_name=executor_model)

    registry = get_registry()
    try:
        executor_provider = registry.get(executor_model)
    except Exception:
        executor_provider = None

    sandbox = None
    from agora.sandbox.docker import get_sandbox_config
    sandbox_cfg = get_sandbox_config()
    if sandbox_cfg.enabled:
        from agora.sandbox.docker import DockerSandbox
        sandbox = DockerSandbox(sandbox_cfg)

    _shared = {
        "agents": agents,
        "moderator": moderator,
        "synthesizer": synthesizer,
        "executor": executor,
        "executor_provider": executor_provider,
        "memory": MemoryStore(),
        "skill_store": SkillStore(),
        "tool_registry": ToolRegistry(sandbox=sandbox),
        "user_profile": _load_user_profile(),
        "concurrent": concurrent,
        "workspace": workspace,
    }
    return _shared


def _create_council() -> Council:
    """Create a new council with fresh context but shared resources."""
    s = _init_shared()
    return Council(
        agents=s["agents"],
        moderator=s["moderator"],
        synthesizer=s["synthesizer"],
        executor=s["executor"],
        executor_provider=s["executor_provider"],
        context=SharedContext(),
        memory=s["memory"],
        skill_store=s["skill_store"],
        tool_registry=s["tool_registry"],
        user_profile=s["user_profile"],
        concurrent=s["concurrent"],
        workspace=s["workspace"],
    )


def get_council(session_id: str | None = None) -> Council:
    """Get or create a council for the given session."""
    sid = session_id or _DEFAULT_SESSION

    if sid in _sessions:
        _sessions.move_to_end(sid)
        return _sessions[sid]

    council = _create_council()
    _sessions[sid] = council

    # Evict oldest if over limit
    while len(_sessions) > _MAX_SESSIONS:
        _sessions.popitem(last=False)

    return council


def reset_council(session_id: str | None = None):
    """Reset a specific session's council, or the default."""
    sid = session_id or _DEFAULT_SESSION
    if sid in _sessions:
        _sessions[sid].reset()
        del _sessions[sid]


def reset_all_councils():
    """Reset all sessions."""
    global _shared
    _sessions.clear()
    _shared = None
