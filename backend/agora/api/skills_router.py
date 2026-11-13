"""The skills page's routes (workspace CR-75 slice 5). Every one needs the install's
terminal token, like the work page: they name this machine's projects and write into
their repositories and the work data."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agora.api.terminal_router import require_terminal_session
from agora.projects.registry import ProjectRegistry
from agora.skills import install as installer
from agora.skills import sources as remote
from agora.skills.inventory import (
    importable, inventory_gaps, library_root, reserved_names, skill_projects, workflow_commands,
)
from agora.skills.library import SkillChanged, SkillLibrary, SkillRefused
from agora.tasks.router import get_project_registry

router = APIRouter(prefix="/skills", dependencies=[Depends(require_terminal_session)])


def _sources(registry: ProjectRegistry) -> list[Path]:
    return [Path(p) for p in registry.workspace_sources()]


def _library(sources: list[Path]) -> SkillLibrary:
    root, reason = library_root(sources)
    return SkillLibrary(root, reason)


def _refused(exc: Exception) -> HTTPException:
    if isinstance(exc, SkillRefused):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "changed" if isinstance(exc, SkillChanged)
                                                               else "refused", "message": str(exc)})
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                         detail={"code": "failed", "message": f"{type(exc).__name__}: {exc}"})


def _extra_places(projects) -> list[tuple[Path, str]]:
    places = []
    for p in projects:
        if p.repo:
            for target in installer.TARGETS.values():
                places.append((Path(p.repo) / target, f"{p.task}: {target}"))
    return places


def _view(sources: list[Path]) -> dict[str, Any]:
    library = _library(sources)
    read = library.read()
    projects = skill_projects(sources)
    commands = workflow_commands(sources)
    installs: dict[str, dict[str, Any]] = {}
    for skill in read.skills:
        row: dict[str, Any] = {}
        for p in projects:
            if not p.repo:
                continue
            states = installer.skill_states(Path(p.repo), skill.name, skill.sha)
            row[p.key] = {"summary": installer.summary(states), "copies": [s.payload() for s in states]}
        installs[skill.name] = row
    return {"library": read.payload(), "projects": [p.payload() for p in projects], "installs": installs,
            "workflow": [c.payload() for c in commands],
            "importable": [f.payload() for f in importable(_extra_places(projects))],
            "reserved": sorted(reserved_names(commands)), "targets": installer.TARGETS}


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (SkillRefused, OSError, UnicodeDecodeError) as exc:
        raise _refused(exc) from exc


@router.get("")
async def skills_view(registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    return await asyncio.to_thread(_view, _sources(registry))


@router.get("/library/{name}")
async def skill_content(name: str, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    return await _run(lambda: _library(_sources(registry)).content(name))


def _found_content(sources: list[Path], path: str) -> dict[str, Any]:
    """What a listed skill outside the library says, read-only. Only a path the view
    itself lists (a workflow command or an importable skill) is read: this route is not
    a way to read arbitrary files."""
    from agora.skills.library import SKILL_FILE, tree_files

    listed = {f.path: f for f in [*workflow_commands(sources),
                                  *importable(_extra_places(skill_projects(sources)))]}
    found = listed.get(path)
    if found is None:
        raise SkillRefused("that path is not one of the skills listed on this page")
    target = Path(found.path)
    if target.is_dir():
        text = (target / SKILL_FILE).read_bytes().decode("utf-8")
        files = [rel for rel, _ in tree_files(target)]
    else:
        text = target.read_bytes().decode("utf-8")
        files = [target.name]
    return {**found.payload(), "content": text, "files": files}


@router.get("/found")
async def found_skill_content(path: str, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    return await _run(_found_content, _sources(registry), path)


class CreateBody(BaseModel):
    name: str
    content: str


class UpdateBody(BaseModel):
    content: str
    #: sha256 of SKILL.md as it was read.
    base: str


class ImportBody(BaseModel):
    #: A path from the view's ``importable`` list; nothing else is accepted.
    path: str
    name: str


class InstallBody(BaseModel):
    project: str
    force: bool = False


@router.post("/library")
async def create_skill(body: CreateBody, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)

    def go():
        return _library(sources).create(body.name, body.content,
                                        reserved=reserved_names(workflow_commands(sources))).payload()
    return await _run(go)


@router.put("/library/{name}")
async def update_skill(name: str, body: UpdateBody,
                       registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    return await _run(lambda: _library(_sources(registry)).update(name, body.content, body.base).payload())


def _installed_in(sources: list[Path], name: str) -> list[str]:
    """Where a copy may be; a project that cannot be read counts, so a delete never
    proceeds on an inventory with a hole in it (codex review)."""
    out = [f"{gap} (cannot tell)" for gap in inventory_gaps(sources)]
    for p in skill_projects(sources):
        if p.repo:
            states = installer.skill_states(Path(p.repo), name, None)
            if any(s.state not in ("absent", "foreign") for s in states):
                out.append(p.task)
    return out


@router.delete("/library/{name}")
async def delete_skill(name: str, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)

    def go():
        _library(sources).delete(name, _installed_in(sources, name))
        return {"name": name, "deleted": True}
    return await _run(go)


@router.post("/import")
async def import_skill(body: ImportBody, registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)

    def go():
        projects = skill_projects(sources)
        offered = {f.path: f for f in importable(_extra_places(projects))}
        found = offered.get(body.path)
        if found is None:
            raise SkillRefused("that path is not one of the skills offered for import")
        return _library(sources).import_from(Path(found.path), body.name,
                                             reserved=reserved_names(workflow_commands(sources))).payload()
    return await _run(go)


# --- skills from outside (CR-98) ---------------------------------------------------------
def _data_dir() -> Path:
    from agora.api import terminal_router

    return terminal_router._data_dir()


class SourceImportBody(BaseModel):
    #: A path from the source's listing.
    path: str
    #: The hash the content route returned for it: proof it was opened as it is now.
    sha: str


@router.get("/sources")
async def skill_sources() -> dict[str, Any]:
    def go():
        return {"sources": [remote.read(_data_dir(), s).payload() for s in remote.SOURCES.values()]}
    return await _run(go)


@router.post("/sources/{source_id}/fetch")
async def fetch_skill_source(source_id: str) -> dict[str, Any]:
    return await _run(lambda: remote.fetch(_data_dir(), remote.source_of(source_id)).payload())


@router.get("/sources/{source_id}/skill")
async def source_skill_content(source_id: str, path: str) -> dict[str, Any]:
    return await _run(lambda: remote.content(_data_dir(), remote.source_of(source_id), path))


@router.post("/sources/{source_id}/import")
async def import_source_skill(source_id: str, body: SourceImportBody,
                              registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)

    def go():
        return remote.import_skill(_data_dir(), remote.source_of(source_id), body.path, body.sha, _library(sources),
                                   reserved=reserved_names(workflow_commands(sources)))
    return await _run(go)


def _repo(sources: list[Path], key: str) -> Path:
    for p in skill_projects(sources):
        if p.key == key:
            if not p.repo:
                raise SkillRefused(p.reason or "this project has no repository")
            return Path(p.repo)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "no_project", "message": "no such project"})


@router.post("/library/{name}/install")
async def install_skill(name: str, body: InstallBody,
                        registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)
    return await _run(lambda: installer.install(_library(sources), _repo(sources, body.project), name, force=body.force))


@router.post("/library/{name}/uninstall")
async def uninstall_skill(name: str, body: InstallBody,
                          registry: ProjectRegistry = Depends(get_project_registry)) -> dict[str, Any]:
    sources = _sources(registry)
    return await _run(lambda: installer.uninstall(_repo(sources, body.project), name, force=body.force))
