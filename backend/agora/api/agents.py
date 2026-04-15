"""Agents API — list, detail, CRUD, test."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agora.api._state import get_council, reset_council
from agora.agents.agent import Agent, PROFILES_DIR

router = APIRouter()

# Utility agents excluded from user-facing lists
_UTILITY = {"moderator", "synthesizer", "executor"}


def _read_profile(name: str) -> dict:
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, f"Agent '{name}' not found")
    return yaml.safe_load(p.read_text()) or {}


@router.get("/agents")
async def list_agents():
    c = get_council()
    return {"agents": [{"name": a.name, "role": a.role, "model": a.model_name} for a in c.agents]}


@router.get("/agents/available")
async def available_agents():
    agents = []
    for p in sorted(PROFILES_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text()) or {}
        name = data.get("name", p.stem)
        if name in _UTILITY:
            continue
        agents.append({"name": name, "role": data.get("role", ""), "profile": p.name})
    return {"agents": agents}


@router.get("/agents/{name}")
async def get_agent(name: str):
    data = _read_profile(name)
    c = get_council()
    active_names = [a.name for a in c.agents]
    return {
        "name": data.get("name", name),
        "role": data.get("role", ""),
        "perspective": data.get("perspective", ""),
        "active": name in active_names,
    }


class AgentUpdateRequest(BaseModel):
    role: str | None = None
    perspective: str | None = None


@router.put("/agents/{name}")
async def update_agent(name: str, req: AgentUpdateRequest):
    data = _read_profile(name)
    if req.role is not None:
        data["role"] = req.role
    if req.perspective is not None:
        data["perspective"] = req.perspective
    p = PROFILES_DIR / f"{name}.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    reset_council()
    return {"status": "updated", "name": name}


class AgentCreateRequest(BaseModel):
    name: str
    role: str
    perspective: str


@router.post("/agents")
async def create_agent(req: AgentCreateRequest):
    p = PROFILES_DIR / f"{req.name}.yaml"
    if p.exists():
        raise HTTPException(409, f"Agent '{req.name}' already exists")
    data = {"name": req.name, "role": req.role, "perspective": req.perspective}
    p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    return {"status": "created", "name": req.name}


@router.delete("/agents/{name}")
async def delete_agent(name: str):
    if name in _UTILITY:
        raise HTTPException(400, "Cannot delete utility agent")
    p = PROFILES_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, f"Agent '{name}' not found")
    p.unlink()
    reset_council()
    return {"status": "deleted", "name": name}


class AgentToggleRequest(BaseModel):
    agents: list[str]


@router.post("/agents/active")
async def set_active_agents(req: AgentToggleRequest):
    from agora.config.settings import get_config
    cfg = get_config()
    cfg.setdefault("council", {})["default_agents"] = req.agents
    reset_council()
    c = get_council()
    return {"agents": [{"name": a.name, "role": a.role, "model": a.model_name} for a in c.agents]}


class AgentTestRequest(BaseModel):
    message: str


@router.post("/agents/{name}/test")
async def test_agent(name: str, req: AgentTestRequest):
    data = _read_profile(name)
    c = get_council()
    agent = Agent(name=name, profile=f"{name}.yaml", model_name=c.agents[0].model_name if c.agents else "gpt4o")

    async def stream():
        messages = [{"role": "user", "content": req.message}]
        async for chunk in agent.stream_respond(messages):
            yield {"event": "token", "data": json.dumps({"agent": name, "role": data.get("role", ""), "content": chunk})}
        yield {"event": "done", "data": json.dumps({"agent": name})}

    return EventSourceResponse(stream())
