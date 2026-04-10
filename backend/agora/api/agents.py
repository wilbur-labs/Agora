"""Agents API."""
from fastapi import APIRouter
from agora.api._state import get_council

router = APIRouter()


@router.get("/agents")
async def list_agents():
    c = get_council()
    return {"agents": [{"name": a.name, "role": a.role, "model": a.model_name} for a in c.agents]}
