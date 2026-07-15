"""FastAPI application."""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agora.api.chat import router as chat_router
from agora.api.agents import router as agents_router
from agora.api.sessions import router as sessions_router
from agora.api.extras import router as extras_router
from agora.api.artifacts import router as artifacts_router
from agora.tasks.router import router as tasks_router
from agora.requirements.router import router as requirements_router
from agora.execution.router import router as execution_router
from agora.execution.router import get_execution_dispatcher
from agora.workspaces.router import router as workspaces_router
from agora.attention.router import router as attention_router
from agora.workflows.router import router as workflows_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    get_execution_dispatcher().resume_queued()
    yield
    if get_execution_dispatcher.cache_info().currsize:
        await get_execution_dispatcher().shutdown()


app = FastAPI(title="Agora", version="0.1.0", description="Multi-perspective AI council", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(chat_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(extras_router, prefix="/api")
app.include_router(artifacts_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(requirements_router, prefix="/api")
app.include_router(execution_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")
app.include_router(attention_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")

# Serve Next.js static export (frontend/out/) if available
_frontend_out = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "out"


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


if _frontend_out.is_dir():
    # Mount _next assets first
    _next_dir = _frontend_out / "_next"
    if _next_dir.is_dir():
        app.mount("/_next", StaticFiles(directory=str(_next_dir)), name="next-assets")

    @app.get("/{path:path}")
    async def serve_frontend(request: Request, path: str = ""):
        # Skip API routes (handled by routers above)
        if path.startswith("api/"):
            return

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
