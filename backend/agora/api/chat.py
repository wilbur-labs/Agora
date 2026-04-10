"""Chat API — SSE streaming multi-agent responses."""
from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agora.api._state import get_council

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


@router.post("/chat")
async def chat(req: ChatRequest):
    council = get_council()

    async def stream():
        async for name, role, chunk in council.stream_discuss(req.message):
            if chunk == "":
                yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": role})}
            else:
                yield {"event": "token", "data": json.dumps({"agent": name, "role": role, "content": chunk})}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(stream())


@router.post("/chat/sync")
async def chat_sync(req: ChatRequest):
    responses = await get_council().discuss(req.message)
    return {"responses": [{"agent": r.agent_name, "role": r.role, "content": r.content} for r in responses]}


@router.post("/chat/reset")
async def chat_reset():
    get_council().reset()
    return {"status": "reset"}
