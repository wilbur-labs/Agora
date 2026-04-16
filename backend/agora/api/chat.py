"""Chat API — SSE streaming multi-agent responses."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agora.api._state import get_council

logger = logging.getLogger(__name__)

router = APIRouter()

# Pending confirmation state for Human-in-the-Loop
_confirm_event: asyncio.Event | None = None
_confirm_result: bool = False
_auto_approve: bool = False


class ChatRequest(BaseModel):
    message: str


class ContinueRequest(BaseModel):
    route: str


class FeedbackRequest(BaseModel):
    message_id: str
    rating: str


class RestoreContextRequest(BaseModel):
    messages: list[dict]


def _agent_events(aiter):
    """Convert agent stream to SSE dicts. Handles both discussion and executor events."""
    async def gen():
        async for name, event_or_role, chunk in aiter:
            # Executor tool events: event_or_role is event_type (tool_call, tool_result, etc.)
            if name == "executor" and event_or_role in ("tool_call", "tool_result", "tool_skipped", "error"):
                yield {"event": event_or_role, "data": json.dumps({"agent": name, "content": chunk})}
            elif name == "executor" and event_or_role == "confirm":
                # Human-in-the-Loop: send confirm event and wait for user response
                yield {"event": "confirm", "data": json.dumps({"agent": name, "content": chunk})}
            elif name == "executor" and event_or_role == "done":
                continue  # skip internal done, we emit our own
            elif name == "executor" and event_or_role == "agent_done":
                yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": "Task Executor"})}
            elif name == "executor" and event_or_role == "text":
                yield {"event": "token", "data": json.dumps({"agent": name, "role": "Task Executor", "content": chunk})}
            elif chunk == "":
                yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": event_or_role})}
            else:
                yield {"event": "token", "data": json.dumps({"agent": name, "role": event_or_role, "content": chunk})}
    return gen()


@router.post("/chat")
async def chat(req: ChatRequest):
    council = get_council()

    async def stream():
        try:
            async for name, role, chunk in council.route(req.message):
                if chunk == "":
                    yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": role})}
                else:
                    yield {"event": "token", "data": json.dumps({"agent": name, "role": role, "content": chunk})}
            yield {"event": "route", "data": json.dumps({"route": council.last_route})}
        except Exception as e:
            logger.exception("Error in chat stream")
            yield {"event": "error", "data": json.dumps({"content": str(e)})}

    return EventSourceResponse(stream())


@router.post("/chat/continue")
async def chat_continue(req: ContinueRequest):
    council = get_council()
    route = req.route.upper()

    # Set up web-based Human-in-the-Loop confirmation
    async def web_confirm(tool_name: str, desc: str, dangerous: bool) -> bool:
        global _confirm_event, _confirm_result, _auto_approve
        if _auto_approve:
            return True
        _confirm_event = asyncio.Event()
        _confirm_result = False
        await _confirm_event.wait()
        _confirm_event = None
        return _confirm_result

    web_confirm.is_auto_approve = lambda: _auto_approve  # type: ignore[attr-defined]
    council.confirm_callback = web_confirm

    async def stream():
        try:
            if route == "DISCUSS":
                async for item in _agent_events(council.stream_discuss()):
                    yield item
            elif route == "QUICK":
                async for item in _agent_events(council.stream_quick()):
                    yield item
            elif route == "EXECUTE":
                async for item in _agent_events(council.stream_execute()):
                    yield item
            yield {"event": "done", "data": json.dumps({"route": route})}
        except Exception as e:
            logger.exception("Error in chat continue stream")
            yield {"event": "error", "data": json.dumps({"content": str(e)})}

    return EventSourceResponse(stream())


@router.post("/chat/sync")
async def chat_sync(req: ChatRequest):
    responses = await get_council().discuss_and_return(req.message)
    return {"responses": [{"agent": r.agent_name, "role": r.role, "content": r.content} for r in responses]}


@router.post("/chat/reset")
async def chat_reset():
    get_council().reset()
    return {"status": "reset"}


@router.post("/chat/feedback")
async def chat_feedback(req: FeedbackRequest):
    return {"status": "ok", "message_id": req.message_id, "rating": req.rating}


@router.post("/chat/restore")
async def chat_restore(req: RestoreContextRequest):
    """Restore backend context from frontend session history."""
    council = get_council()
    council.reset()
    for msg in req.messages:
        msg_type = msg.get("type", "")
        content = msg.get("content", "")
        if msg_type == "user":
            council.context.add_user(content)
        elif msg_type == "agent" and msg.get("agent"):
            council.context.add_agent(msg["agent"], content)
    return {"status": "ok", "message_count": len(council.context.messages)}


class ConfirmResponse(BaseModel):
    approved: bool


@router.post("/chat/confirm")
async def chat_confirm(req: ConfirmResponse):
    """User responds to a Human-in-the-Loop confirmation request."""
    global _confirm_event, _confirm_result
    if _confirm_event is None:
        return {"status": "no_pending_confirmation"}
    _confirm_result = req.approved
    _confirm_event.set()
    return {"status": "ok"}


class AutoApproveRequest(BaseModel):
    enabled: bool


@router.post("/chat/auto-approve")
async def chat_auto_approve(req: AutoApproveRequest):
    """Toggle auto-approve mode for tool confirmations."""
    global _auto_approve
    _auto_approve = req.enabled
    return {"status": "ok", "auto_approve": _auto_approve}


@router.get("/chat/auto-approve")
async def chat_auto_approve_status():
    """Get current auto-approve status."""
    return {"auto_approve": _auto_approve}
