"""Sessions API — CRUD for chat sessions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agora.api import sessions_db as db

router = APIRouter()


class SaveSessionRequest(BaseModel):
    title: str = ""
    messages: list[dict] = []


@router.get("/sessions")
async def list_sessions():
    return {"sessions": db.list_sessions()}


@router.post("/sessions")
async def create_session(req: SaveSessionRequest):
    sid = db.create_session(req.title)
    if req.messages:
        db.update_session_messages(sid, req.messages)
    return {"id": sid, "title": req.title}


@router.get("/sessions/{sid}")
async def get_session(sid: str):
    s = db.get_session(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


class UpdateMessagesRequest(BaseModel):
    messages: list[dict]
    title: str | None = None


@router.put("/sessions/{sid}")
async def update_session(sid: str, req: UpdateMessagesRequest):
    s = db.get_session(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    db.update_session_messages(sid, req.messages)
    if req.title is not None:
        db.update_session_title(sid, req.title)
    return {"status": "updated"}


@router.delete("/sessions/{sid}")
async def delete_session(sid: str):
    db.delete_session(sid)
    return {"status": "deleted"}


class ShareRequest(BaseModel):
    messages: list[dict]


@router.post("/chat/share")
async def share_chat(req: ShareRequest):
    share_id = db.create_share(req.messages)
    return {"id": share_id}


@router.get("/shared/{share_id}")
async def get_shared(share_id: str):
    s = db.get_share(share_id)
    if not s:
        raise HTTPException(404, "Share not found")
    return s
