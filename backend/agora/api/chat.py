"""Chat API — SSE streaming multi-agent responses."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agora.api._state import get_council, reset_council

logger = logging.getLogger(__name__)

router = APIRouter()

# Pending confirmation state for Human-in-the-Loop
_confirm_event: asyncio.Event | None = None
_confirm_result: bool = False
_auto_approve: bool = False


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ContinueRequest(BaseModel):
    route: str
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    message_id: str
    rating: str


class RestoreContextRequest(BaseModel):
    messages: list[dict]
    session_id: str | None = None


def _agent_events(aiter):
    """Convert agent stream to SSE dicts. Handles both discussion and executor events."""
    async def gen():
        async for name, event_or_role, chunk in aiter:
            # Tool events from any agent (executor, scout research phase, etc.)
            if event_or_role in ("tool_call", "tool_result", "tool_skipped", "error"):
                yield {"event": event_or_role, "data": json.dumps({"agent": name, "content": chunk})}
            elif event_or_role == "confirm":
                yield {"event": "confirm", "data": json.dumps({"agent": name, "content": chunk})}
            elif event_or_role == "artifact_created":
                yield {"event": "artifact_created", "data": json.dumps({"path": chunk})}
            elif event_or_role == "done":
                continue  # skip internal done, we emit our own
            elif event_or_role == "agent_done":
                yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": "Task Executor" if name == "executor" else event_or_role})}
            elif event_or_role == "text":
                yield {"event": "token", "data": json.dumps({"agent": name, "role": "Task Executor" if name == "executor" else "Researcher", "content": chunk})}
            elif chunk == "":
                yield {"event": "agent_done", "data": json.dumps({"agent": name, "role": event_or_role})}
            else:
                yield {"event": "token", "data": json.dumps({"agent": name, "role": event_or_role, "content": chunk})}
    return gen()


@router.post("/chat")
async def chat(req: ChatRequest):
    council = get_council(req.session_id)

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
    council = get_council(req.session_id)
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


class ResetRequest(BaseModel):
    session_id: str | None = None


@router.post("/chat/reset")
async def chat_reset(req: ResetRequest = ResetRequest()):
    reset_council(req.session_id)
    from agora.api.artifacts import clear_artifacts
    clear_artifacts()
    return {"status": "reset"}


@router.post("/chat/feedback")
async def chat_feedback(req: FeedbackRequest):
    return {"status": "ok", "message_id": req.message_id, "rating": req.rating}


@router.post("/chat/restore")
async def chat_restore(req: RestoreContextRequest):
    """Restore backend context from frontend session history."""
    council = get_council(req.session_id)
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
