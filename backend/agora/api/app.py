"""FastAPI application."""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from agora.api.local_guard import LocalOriginGuard, dev_origins
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agora import __version__
from agora.config.settings import assert_no_database_ambiguity, get_config
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
from agora.api.kickoff_router import router as kickoff_router
from agora.api.terminal_router import get_terminal_manager, router as terminal_router

def _load_display_language() -> None:
    """Adopt the persisted ``display_language``, if there is a valid one.

    Never raises. A missing row leaves the current value (the default ``zh``); an
    unreadable database or a value outside the catalog leaves it too and is
    reported on stderr. ``say()`` itself never reads the database — doing that lazily
    would make every test that renders a message open whatever database the process
    resolves (``tests/conftest.py``).
    """
    try:
        from agora.config.settings import resolve_control_plane_db_path
        from agora.messages import SETTING_DISPLAY_LANGUAGE, set_display_language
        from agora.projects.store import ProjectStore

        # The store alone, not a ProjectRegistry: constructing a registry imports
        # the YAML source and creates directories under every project root, none of
        # which reading one setting needs.
        stored = ProjectStore(resolve_control_plane_db_path(get_config())).get_setting(
            SETTING_DISPLAY_LANGUAGE
        )
        if stored is not None:
            set_display_language(stored)
    except Exception as exc:  # noqa: BLE001 - a display preference never blocks startup
        try:
            print(
                f"agora: display language could not be loaded, keeping the default: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001, S110 - nowhere left to report it to
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Which database this process is about to use, and — first — whether that
    # question has one answer at all.
    #
    # The control-plane database's relative paths are anchored to the directory
    # holding config.yaml, not to the working directory (that is the resolver's list,
    # not every path in the file). That rule changed under installations that already
    # exist, so an install whose data sits at the old working-directory path would
    # otherwise come up on a *different* file and present a different project and
    # task list, which reads exactly like data loss. `assert_no_database_ambiguity`
    # refuses to start in that case and names both paths.
    #
    # This one is deliberately NOT wrapped: it is the check, and a check that cannot
    # stop the process is a comment. The print below it is the opposite — a line
    # whose only job is to tell you something must never be the thing that fails.
    db_path = assert_no_database_ambiguity(get_config())
    try:
        print(f"agora: control-plane database {db_path}", file=sys.stderr)
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
    # The reader's language for backend-written text (CR-71), loaded before anything
    # below can write a run-log note or an attention item. Wrapped: a language that
    # cannot be read is a display preference left at its default, never a reason to
    # refuse to start — and it is said on stderr rather than silently assumed.
    _load_display_language()
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
    # Terminals end with the server: their process trees are in job objects that die
    # with it anyway; closing them first lets each one report its exit (CR-30).
    if get_terminal_manager.cache_info().currsize:
        get_terminal_manager().close_all()
    await supervisor.shutdown()
    if get_execution_dispatcher.cache_info().currsize:
        await get_execution_dispatcher().shutdown()


app = FastAPI(
    title="Agora",
    version=__version__,
    description="Local-first Task delivery control plane",
    lifespan=lifespan,
)
# Only the origins a development install names (AGORA_DEV_ORIGINS, e.g. the Next dev
# server on :3000). It was "*", which let any page the person opened call this API from
# their browser (CR-34). Same-origin requests — the served UI — need no CORS at all.
app.add_middleware(CORSMiddleware, allow_origins=dev_origins(), allow_methods=["*"], allow_headers=["*"])
app.include_router(tasks_router, prefix="/api")
app.include_router(requirements_router, prefix="/api")
app.include_router(execution_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")
app.include_router(attention_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(control_plane_router, prefix="/api")
app.include_router(task_discovery_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(kickoff_router, prefix="/api")
app.include_router(terminal_router, prefix="/api")


# Field names whose VALUE must never be reflected back in a 422 body. FastAPI's default
# request-validation handler echoes the offending `input` for each error — and when a
# body-level (model) validator fails, `input` is the WHOLE parsed body, which for the
# remote-environment endpoints carries the keyring `password`. The credential invariant
# (adr/0001) is that a password lives in the request body once and is never returned or
# logged, so the value is redacted out of every validation-error response here rather
# than relying on each endpoint. Redaction only touches the error detail's echoed input;
# the status code and which fields failed are unchanged.
_REDACTED_ERROR_FIELDS = frozenset({"password"})
_REDACTED_PLACEHOLDER = "[redacted]"
# Fixed, value-free message used in place of a validator's own message when that error
# could carry a sensitive value. pydantic v2 interpolates the offending value into as
# many as three echoed places — `input`, `ctx` (the raised exception), and `msg` (its
# text) — so redacting only `input` is not enough (adr/0001).
_REDACTED_MESSAGE = "invalid value for a sensitive field"


def _raw_mentions_secret(value: str | bytes) -> bool:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    return any(field in text.lower() for field in _REDACTED_ERROR_FIELDS)


def _carries_secret(value: Any) -> bool:
    """Whether an echoed input could hold a secret, at any depth. A raw string/bytes
    body (sent as text/plain, or otherwise not parsed into structure) cannot be
    inspected field by field, so one that so much as names a sensitive field counts."""
    if isinstance(value, dict):
        return any(
            key in _REDACTED_ERROR_FIELDS or _carries_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_carries_secret(item) for item in value)
    if isinstance(value, (str, bytes)):
        return _raw_mentions_secret(value)
    return False


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED_PLACEHOLDER if key in _REDACTED_ERROR_FIELDS else _redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, (str, bytes)) and _raw_mentions_secret(value):
        return _REDACTED_PLACEHOLDER  # unstructured: the whole thing goes
    return value


def _scrub_validation_errors(errors: list) -> list:
    scrubbed = []
    for error in errors:
        error = dict(error)
        loc = error.get("loc") or ()
        field_level_sensitive = bool(loc) and loc[-1] in _REDACTED_ERROR_FIELDS
        raw_input = error.get("input")
        # Searched at any depth: a malformed body (an array where an object was
        # expected, an object nested in one) is echoed whole, secrets and all.
        body_carries_secret = _carries_secret(raw_input)
        # An error can carry the secret value in up to three echoed channels, so treat
        # them together. A field-level error on a sensitive field: its `input` IS the
        # value. A body-level (model_validator) error: `input` is the whole parsed body,
        # so redact the sensitive keys inside it. Either way, pydantic also puts the
        # value into `ctx["error"]` (the raised exception, stringified by
        # jsonable_encoder) and into `msg` (its text) — so a validator that interpolated
        # the value would leak it there past an input-only redaction. This handler is
        # sold as the global net, so close all three whenever the error could carry a
        # secret, rather than trusting every validator message to stay value-free.
        if field_level_sensitive and "input" in error:
            error["input"] = _REDACTED_PLACEHOLDER
        elif body_carries_secret:
            error["input"] = _redact_secrets(raw_input)
        if field_level_sensitive or body_carries_secret:
            error.pop("ctx", None)  # ctx["error"] is the raised exception, may hold the value
            if "msg" in error:
                error["msg"] = _REDACTED_MESSAGE  # msg text may interpolate the value
        scrubbed.append(error)
    return scrubbed


@app.exception_handler(RequestValidationError)
async def _redacting_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # jsonable_encoder first (as FastAPI's own handler does) so a ValueError carried in a
    # model-validator error's ctx becomes a plain string; then redact the sensitive
    # values out of the now-pure-dict structure.
    return JSONResponse(status_code=422, content={"detail": _scrub_validation_errors(jsonable_encoder(exc.errors()))})


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
# Outermost, so it sees every request and WebSocket before anything else answers (CR-34).
# Registered after every other middleware on purpose: the last one added runs first.
app.add_middleware(LocalOriginGuard)

_frontend_out = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "out"


#: Which installation this is, as the launcher determined it (CR-31): ``stable`` only
#: when the code sits exactly on a release tag with a clean tree, ``dev`` otherwise.
#: Anything else — including no launcher at all — is ``unknown``, never a guess.
_INSTANCES = {"stable", "dev"}


def instance_of(environ=None) -> str:
    value = (environ if environ is not None else os.environ).get("AGORA_INSTANCE", "").strip()
    return value if value in _INSTANCES else "unknown"


@app.get("/health")
async def health():
    # Which configuration file this server was started with, so a launcher asked for the
    # development install never mistakes a normal server on that port for it (CR-31).
    config = Path(os.environ.get("AGORA_CONFIG_PATH", "").strip() or "config.yaml").name
    return {"status": "ok", "version": __version__, "instance": instance_of(), "config": config}


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
