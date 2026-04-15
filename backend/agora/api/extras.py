"""Skills & Memory API."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

import yaml

from agora.api._state import get_council

router = APIRouter()


@router.get("/skills")
async def list_skills():
    c = get_council()
    skills = []
    for s in c.skill_store.skills:
        skills.append({
            "name": s.name,
            "type": s.type,
            "trigger": s.trigger,
            "steps": s.steps,
            "lessons": s.lessons,
            "success_count": s.success_count,
            "fail_count": s.fail_count,
        })
    return {"skills": skills}


@router.get("/memory")
async def get_memory():
    c = get_council()
    return {"memory": c.memory.get_injection_text(), "profile": c.user_profile}


@router.get("/profile")
async def get_profile():
    from agora.api._state import USER_PROFILE_PATH
    if USER_PROFILE_PATH and USER_PROFILE_PATH.exists():
        data = yaml.safe_load(USER_PROFILE_PATH.read_text()) or {}
        return {"profile": data}
    return {"profile": {}}


class ProfileUpdateRequest(BaseModel):
    profile: dict


@router.put("/profile")
async def update_profile(req: ProfileUpdateRequest):
    from agora.api._state import save_user_profile
    for k, v in req.profile.items():
        save_user_profile(k, str(v))
    return {"status": "updated"}
