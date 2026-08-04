"""FastAPI application."""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agora import __version__
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

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_control_plane_store()
    get_execution_dispatcher().resume_queued()
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
_RETIRED_UI_PATHS = ("chat", "agents", "skills", "settings", "shared")
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
