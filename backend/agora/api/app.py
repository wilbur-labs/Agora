"""FastAPI application."""
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agora import __version__
from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.tasks.router import router as tasks_router
from agora.requirements.router import router as requirements_router
from agora.execution.router import router as execution_router
from agora.execution.router import get_execution_dispatcher
from agora.workspaces.router import router as workspaces_router
from agora.attention.router import router as attention_router
from agora.workflows.router import router as workflows_router
from agora.workflows.router import get_workflow_supervisor
from agora.control_plane.router import (
    initialize_control_plane_store,
    router as control_plane_router,
    task_discovery_router,
)
from agora.api.settings_router import router as settings_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Which database this process is about to use, absolute, before it touches it.
    #
    # It is not a given. `resolve_control_plane_db_path` falls back to paths that are
    # relative to the *working directory*, and the shipped config.yaml names one
    # (`memory.data_dir: ./data`) — so `make dev` from the repository root and from
    # `backend/` are two different databases, presenting two different project and
    # task lists with nothing on screen to say so. That is a real defect and it is
    # not fixed here (moving the path would silently switch a developer onto their
    # other database; it needs a startup check that refuses rather than switches).
    # What is fixed here is that it is no longer *invisible*: one line naming the
    # file makes "which one am I on" answerable, which is also the first thing
    # anyone will need when the resolution itself is changed.
    # Wrapped, because a line whose only job is to tell you something must not be
    # able to stop the server from starting. `initialize_control_plane_store()` below
    # reads the same configuration and will fail loudly on its own if it is broken;
    # this one has no business being the first thing that does.
    try:
        print(
            "agora: control-plane database "
            f"{resolve_control_plane_db_path(get_config()).resolve()}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001 - a diagnostic may never be the failure
        # The fallback is wrapped too. The first branch's most likely cause is that
        # this stream cannot be written to at all — a closed or detached stderr under
        # a process manager — and reporting that failure *to the same stream* raises
        # again and takes down the startup the try was there to protect. A diagnostic
        # that cannot be delivered is simply not delivered.
        try:
            print(
                f"agora: could not report the control-plane database path: {exc}",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001, S110 - nowhere left to report it to
            pass
    initialize_control_plane_store()
    # One call, because the two steps behind it are ordered by a real dependency
    # and not by taste: reconcile_reviews_owed raises the gates that resume_queued
    # judges the persisted queue against, and both have to finish before the API
    # serves. The order lives in the dispatcher (ExecutionDispatcher.start_recovery,
    # and resume_queued refuses to run first), so it cannot be lost by editing this
    # file.
    recovery = get_execution_dispatcher().start_recovery()
    if recovery["reviews_owed_unreadable"] is not None:
        # A reconciliation that could not run is a fact, not a no-op: the tasks it
        # would have recorded a blocker for stay refused with `review_owed` until a
        # later start reads them, and nobody can act on that without being told.
        # stderr is the operator's channel at startup (uvicorn's console), and it is
        # the only one available here — the failure is the database read itself, so
        # there is nowhere durable to write it.
        print(
            "agora: startup review-owed reconciliation could not run: "
            f"{recovery['reviews_owed_unreadable']}",
            file=sys.stderr,
        )
    for unrecorded in recovery["reviews_owed_unrecorded"]:
        # The same fact one task at a time: this run owes a review and the human
        # blocker saying so could not be written. The gate itself is safe without it
        # — `_resume_refusal` derives the refusal (M1) — so what is lost is the inbox
        # item, which is exactly the thing an operator would otherwise wait for.
        print(
            "agora: startup review-owed reconciliation could not record a blocker for "
            f"task {unrecorded.get('task_id')} run {unrecorded.get('run_id')} "
            f"(cause {unrecorded.get('cause')}): {unrecorded.get('error')}",
            file=sys.stderr,
        )
    supervisor = get_workflow_supervisor()
    supervisor.start()
    yield
    await supervisor.shutdown()
    if get_execution_dispatcher.cache_info().currsize:
        await get_execution_dispatcher().shutdown()


app = FastAPI(
    title="Agora",
    version=__version__,
    description="Local-first Task delivery control plane",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(tasks_router, prefix="/api")
app.include_router(requirements_router, prefix="/api")
app.include_router(execution_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")
app.include_router(attention_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(control_plane_router, prefix="/api")
app.include_router(task_discovery_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


_RETIRED_API_PREFIXES = (
    "chat",
    "agents",
    "artifacts",
    "sessions",
    "shared",
    "skills",
    "memory",
    "profile",
)
_RETIRED_UI_PATHS = ("chat", "agents", "skills", "shared")
_RETIRED_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_RETIRED_DETAIL = {
    "code": "legacy_council_retired",
    "message": (
        "The Agora 0.5 autonomous council is retired. "
        "Use the authenticated Task Control Plane."
    ),
}


def _is_retired_api_path(path: str) -> bool:
    return any(
        path == f"/api/{prefix}" or path.startswith(f"/api/{prefix}/")
        for prefix in _RETIRED_API_PREFIXES
    )


@app.middleware("http")
async def _retire_legacy_council_before_cors(request: Request, call_next):
    if _is_retired_api_path(request.url.path):
        return JSONResponse(status_code=410, content={"detail": _RETIRED_DETAIL})
    return await call_next(request)


async def _legacy_council_retired() -> None:
    raise HTTPException(
        status_code=410,
        detail=_RETIRED_DETAIL,
    )


async def _redirect_retired_ui() -> RedirectResponse:
    return RedirectResponse(url="/control-plane", status_code=308)


for _legacy_prefix in _RETIRED_API_PREFIXES:
    app.add_api_route(
        f"/api/{_legacy_prefix}",
        _legacy_council_retired,
        methods=list(_RETIRED_METHODS),
        include_in_schema=False,
        name=f"retired_{_legacy_prefix}_root",
    )
    app.add_api_route(
        f"/api/{_legacy_prefix}/{{legacy_path:path}}",
        _legacy_council_retired,
        methods=list(_RETIRED_METHODS),
        include_in_schema=False,
        name=f"retired_{_legacy_prefix}_nested",
    )


for _legacy_ui_path in _RETIRED_UI_PATHS:
    app.add_api_route(
        f"/{_legacy_ui_path}",
        _redirect_retired_ui,
        methods=["GET"],
        include_in_schema=False,
        name=f"retired_{_legacy_ui_path}_ui",
    )
    app.add_api_route(
        f"/{_legacy_ui_path}/{{legacy_path:path}}",
        _redirect_retired_ui,
        methods=["GET"],
        include_in_schema=False,
        name=f"retired_{_legacy_ui_path}_nested_ui",
    )

# Serve Next.js static export (frontend/out/) if available
_frontend_out = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "out"


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


if _frontend_out.is_dir():
    # Mount _next assets first
    _next_dir = _frontend_out / "_next"
    if _next_dir.is_dir():
        app.mount("/_next", StaticFiles(directory=str(_next_dir)), name="next-assets")

    @app.get("/{path:path}")
    async def serve_frontend(request: Request, path: str = ""):
        # Skip API routes (handled by routers above)
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")

        # Try exact HTML file (e.g. /agents -> agents.html)
        html_file = _frontend_out / f"{path}.html" if path else _frontend_out / "index.html"
        if html_file.exists():
            return FileResponse(str(html_file))

        # Try as directory index
        dir_index = _frontend_out / path / "index.html"
        if dir_index.exists():
            return FileResponse(str(dir_index))

        # Try as static file
        static_file = _frontend_out / path
        if static_file.exists() and static_file.is_file():
            return FileResponse(str(static_file))

        # Fallback to index.html
        return FileResponse(str(_frontend_out / "index.html"))
