"""REST API for the human-attention inbox."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.concurrency import run_in_threadpool

from agora.tasks.router import get_task_store

from .models import (
    AttentionCount, AttentionItem, AttentionKind, AttentionState,
    CancelAttentionRequest, CreateAttentionRequest, RespondAttentionRequest,
)
from .store import AttentionConflictError, AttentionNotFoundError, AttentionStore, AttentionValidationError
from .bridges.models import BridgeEventReceipt, BridgeEventRequest


router = APIRouter(tags=["attention"])


@lru_cache(maxsize=1)
def get_attention_store() -> AttentionStore:
    return AttentionStore(get_task_store())


@router.post("/attention/bridge-events", response_model=BridgeEventReceipt)
async def capture_bridge_event(
    request: BridgeEventRequest, store: AttentionStore = Depends(get_attention_store),
):
    try:
        return await run_in_threadpool(store.create_bridge_event, request)
    except AttentionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AttentionValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/attention", response_model=AttentionItem, status_code=status.HTTP_201_CREATED)
async def create_attention(request: CreateAttentionRequest, store: AttentionStore = Depends(get_attention_store)):
    try:
        return await run_in_threadpool(store.create, request)
    except AttentionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except AttentionValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/attention", response_model=list[AttentionItem])
async def list_attention(
    project_id: str | None = Query(default=None, max_length=128),
    task_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    state_filter: AttentionState | None = Query(default=None, alias="state"),
    kind: AttentionKind | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: AttentionStore = Depends(get_attention_store),
):
    return await run_in_threadpool(
        lambda: store.list(project_id=project_id, task_id=task_id, run_id=run_id,
                           state=state_filter, kind=kind, limit=limit, offset=offset)
    )


@router.get("/attention/count", response_model=AttentionCount)
async def attention_count(
    project_id: str | None = Query(default=None, max_length=128),
    store: AttentionStore = Depends(get_attention_store),
):
    return AttentionCount(open=await run_in_threadpool(lambda: store.open_count(project_id=project_id)))


@router.get("/attention/{item_id}", response_model=AttentionItem)
async def get_attention(
    item_id: Annotated[str, Path(max_length=128)], store: AttentionStore = Depends(get_attention_store)
):
    try:
        return await run_in_threadpool(store.require, item_id)
    except AttentionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attention item not found") from None


@router.post("/attention/{item_id}/respond", response_model=AttentionItem)
async def respond_attention(
    item_id: Annotated[str, Path(max_length=128)], request: RespondAttentionRequest,
    store: AttentionStore = Depends(get_attention_store),
):
    try:
        return await run_in_threadpool(store.respond, item_id, request)
    except AttentionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attention item not found") from None
    except AttentionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

@router.post("/attention/{item_id}/cancel", response_model=AttentionItem)
async def cancel_attention(
    item_id: Annotated[str, Path(max_length=128)], request: CancelAttentionRequest,
    store: AttentionStore = Depends(get_attention_store),
):
    try:
        return await run_in_threadpool(store.cancel, item_id, request)
    except AttentionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attention item not found") from None
    except AttentionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
