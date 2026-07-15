"""Opt-in background reconciliation for auto-dispatch workflows."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from .models import WorkflowState
from .orchestrator import WorkflowOrchestrator
from .store import WorkflowStore


class WorkflowSupervisor:
    def __init__(self, workflows: WorkflowStore, orchestrator: WorkflowOrchestrator, *, interval_seconds: float = 5):
        if interval_seconds < 1 or interval_seconds > 300:
            raise ValueError("workflow supervisor interval must be between 1 and 300 seconds")
        self.workflows = workflows
        self.orchestrator = orchestrator
        self.interval_seconds = interval_seconds
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            # The cached supervisor can be started by multiple TestClient event
            # loops (and by an application restart), so lifecycle primitives
            # must be created in the loop that will use them.
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="agora-workflow-supervisor")

    async def shutdown(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._stop = None

    async def run_once(self) -> None:
        for summary in self.workflows.list(state=WorkflowState.ACTIVE, limit=500):
            if not summary.auto_dispatch:
                continue
            try:
                await self.orchestrator.dispatch(summary.workflow_id)
            except Exception as exc:
                # Recording is best effort: a workflow can disappear between
                # listing and dispatch, and one audit failure must not stop the
                # supervisor from servicing the remaining workflows.
                with suppress(Exception):
                    self.workflows.record_scheduler_error(
                        summary.workflow_id, "", error=f"automatic dispatch failed: {type(exc).__name__}: {exc}",
                    )

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            return
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except (TimeoutError, asyncio.TimeoutError):
                pass
