"""Import projects from the maintainer's hand-written YAML into the store.

The YAML (`.agora/projects.yaml`) used to be the live store. It is now an import
source and nothing else:

* It is never written, rewritten, moved or deleted by Agora. It is the
  maintainer's own file listing real projects, and a bad import has to remain
  comparable against it.
* Import is idempotent and keyed by ``project_id``. Running it twice does not
  duplicate a project and does not undo changes made through Agora.
* An unreadable or malformed file is an error, never "no projects". Presenting an
  import failure as an empty project list is the specific failure this module
  exists to prevent.

Both-sides-differ policy
------------------------
When a project exists in the file and in the store with different values:

* If nothing has changed the stored row through Agora since it was imported, the
  file is the newer statement of intent and the row is updated from it. "Nothing
  has changed it" is not inferred from the values: every Agora-side write bumps a
  revision counter on the row, and the import compares that counter against the
  value it recorded at import time. Comparing values alone would call a project
  that was changed through Agora and changed back untouched, and silently reapply
  the file over a decision somebody made in the product.
* Otherwise the stored row wins and the difference is reported as a conflict.
  The database is the source of truth, and it is the only place that can hold
  agent assignments and charters set inside the product; silently overwriting
  those with a file that never saw them would destroy real work.

The comparison and the replacement happen inside one transaction in the store
(``ProjectStore.apply_import``), because they are one decision: constructing a
registry runs an import, so anything read and then written in two steps races
ordinary API traffic.

Either way the file on disk is left exactly as it was.

What counts as a malformed source
---------------------------------
Anything that cannot be read as one unambiguous list of projects, including:

* duplicate mapping keys — YAML's last-one-wins would silently drop the earlier
  block and present a shorter list as authoritative, which is data loss reported
  as success;
* a ``current`` that names a project the file does not list — the pointer cannot
  be migrated, and dropping it would report a semantically invalid source as a
  successful import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import (
    DEFAULT_REVIEWER_AGENT,
    DEFAULT_WORKER_AGENT,
    validate_agents,
    validate_project_id,
)
from .store import (
    SETTING_CURRENT_PROJECT,
    SETTING_PROJECTS_BASE,
    ProjectStore,
)


class ProjectImportError(RuntimeError):
    """The import source could not be read or understood.

    Raised instead of returning an empty result: a caller that cannot tell these
    apart will show "no projects" for a broken file.

    ``source_state`` records what is actually known about the file: ``present``
    only when the whole file was read as text and then did not make sense,
    ``absent`` only for a definite "no such file", and ``unknown`` whenever the
    read did not complete — a permission error, an I/O error, or bytes that are not
    decodable text. Only ``absent`` may be treated as "there is no source", and
    ``unknown`` may not be treated as either.

    The default is ``present`` because most raise sites are content checks that run
    after the text is in hand. Any raise site reached before the read completes
    must pass ``source_state`` explicitly.
    """

    def __init__(self, source: Path | str, reason: str, *, source_state: str = "present"):
        self.source = str(source)
        self.reason = reason
        self.source_state = source_state
        super().__init__(f"Could not import projects from {self.source}: {reason}")


class ProjectImportSourceMissing(ProjectImportError):
    """The import source definitely does not exist.

    Raised only for ``FileNotFoundError`` when opening it. Every other failure to
    open — a permission error, a name that is not a file, an I/O error — is
    unknown, not absent, and must not become "there is no source": that is how an
    unreadable file ends up seeding default projects over a real configuration and
    looking exactly like a machine that never had one.
    """

    def __init__(self, source: Path | str, reason: str = "no such file"):
        super().__init__(source, reason, source_state="absent")


class _DuplicateKeyError(yaml.constructor.ConstructorError):
    """A mapping in the source lists the same key twice."""

    def __init__(self, key: Any, node: Any):
        self.duplicate_key = key
        super().__init__(None, None, f"duplicate key {key!r}", getattr(node, "start_mark", None))


class _StrictLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses duplicate mapping keys.

    ``yaml.safe_load`` applies last-one-wins, so a file with two ``alpha:`` blocks
    parses cleanly and yields one project. For an import source that is data loss
    reported as success: the shorter list would be presented as the maintainer's
    intent. A file that cannot be read unambiguously is a malformed source, not a
    smaller one.
    """


def _construct_mapping_without_duplicates(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        # PyYAML's own ``construct_mapping`` hashes the key here and raises a
        # ``ConstructorError`` when it cannot; this loop replaced that method and left
        # the check behind. Without it a legal-but-unsupported complex key
        # (``? [a, b]``) reaches ``key in mapping`` and raises a bare ``TypeError``,
        # which is not a ``yaml.YAMLError`` — so every caller's ``except
        # yaml.YAMLError`` misses it and a malformed file becomes an exception out of a
        # reader that promises never to raise. Measured, not reasoned: a one-line
        # document reproduces it.
        try:
            hash(key)
        except TypeError:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found unhashable key", key_node.start_mark,
            ) from None
        if key in mapping:
            raise _DuplicateKeyError(key, key_node)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_without_duplicates
)

#: The same loader, for readers outside this module. `workspace_tasks.py` parses a
#: workspace task's `task.yaml`, where a duplicated `goal:` would be exactly the loss
#: described above — one of two goals shown as *the* goal, with nothing saying a second
#: existed. Exported rather than copied: two strict loaders are two things to keep in
#: step, and the one that drifts decides what a file is allowed to say.
StrictLoader = _StrictLoader
DuplicateKeyError = _DuplicateKeyError


@dataclass(frozen=True)
class ProjectImportConflict:
    """A project the file and the store both describe, differently."""

    project_id: str
    fields: tuple[str, ...]

    def describe(self) -> str:
        return (
            f"{self.project_id}: kept the values stored in Agora; the import file "
            f"differs in {', '.join(self.fields)}"
        )


@dataclass(frozen=True)
class ProjectImportResult:
    source: str
    imported: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    conflicts: tuple[ProjectImportConflict, ...] = ()
    current_project_id: str | None = None
    projects_base: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "imported": list(self.imported),
            "updated": list(self.updated),
            "unchanged": list(self.unchanged),
            "conflicts": [
                {"project_id": c.project_id, "fields": list(c.fields)} for c in self.conflicts
            ],
            "current_project_id": self.current_project_id,
            "projects_base": self.projects_base,
        }


@dataclass
class _ParsedProject:
    project_id: str
    fields: dict[str, Any]
    root: Path
    workspaces: dict[str, Path]


@dataclass
class _ParsedDocument:
    projects: list[_ParsedProject] = field(default_factory=list)
    current: str | None = None
    projects_base: str | None = None


def read_project_yaml(path: str | Path, *, project_root: Path) -> _ParsedDocument:
    """Parse the import source, or raise ``ProjectImportError``.

    Every failure mode is an error, including a file that parses but lists no
    projects: this file's only purpose is to list projects, so a copy listing
    none is far more likely truncated than intentional.

    The file is opened and read, not probed. ``Path.exists()`` answers False for
    stat failures that are not absence at all, so only ``FileNotFoundError``
    yields :class:`ProjectImportSourceMissing`; every other failure to complete the
    read — permission, I/O, undecodable bytes — is unknown and fails closed.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectImportSourceMissing(source) from exc
    except UnicodeDecodeError as exc:
        # The read itself did not complete, so nothing was observed about the
        # source beyond "some bytes exist". Reporting it as present would claim an
        # observation that was never made: a file whose bytes cannot be decoded
        # has not been read, and "present" here means read. Unknown, not present.
        raise ProjectImportError(
            source, "not valid UTF-8 text", source_state="unknown"
        ) from exc
    except OSError as exc:
        raise ProjectImportError(
            source, f"unreadable ({type(exc).__name__})", source_state="unknown"
        ) from exc

    try:
        # _StrictLoader is a SafeLoader subclass, so this constructs no arbitrary
        # objects; it only refuses what safe_load would silently accept.
        document = yaml.load(text, Loader=_StrictLoader)
    except _DuplicateKeyError as exc:
        raise ProjectImportError(
            source,
            f"duplicate key {exc.duplicate_key!r}: the same key is listed twice, so the "
            "file does not say unambiguously what it contains",
        ) from exc
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise ProjectImportError(source, f"invalid YAML ({problem})") from exc

    if document is None:
        raise ProjectImportError(source, "file is empty")
    if not isinstance(document, dict):
        raise ProjectImportError(source, "top level is not a mapping")

    raw_projects = document.get("projects")
    if raw_projects is None:
        raise ProjectImportError(source, "no 'projects' mapping")
    if not isinstance(raw_projects, dict):
        raise ProjectImportError(source, "'projects' is not a mapping")
    if not raw_projects:
        raise ProjectImportError(source, "'projects' mapping is empty")

    parsed = _ParsedDocument()
    for project_id, entry in raw_projects.items():
        parsed.projects.append(_parse_project(source, str(project_id), entry, project_root))

    current = document.get("current")
    if current is not None and not isinstance(current, str):
        raise ProjectImportError(source, "'current' is not a project id")
    if current is not None and current not in {p.project_id for p in parsed.projects}:
        # There is nothing to migrate the pointer to. Dropping it and letting the
        # registry pick something else would report a semantically invalid file as
        # a successful import of a pointer it never carried.
        raise ProjectImportError(
            source, f"'current' names '{current}', which the file does not list as a project"
        )
    parsed.current = current

    base = document.get("projects_base")
    if base is not None and not isinstance(base, str):
        raise ProjectImportError(source, "'projects_base' is not a path")
    parsed.projects_base = base
    return parsed


def _parse_project(
    source: Path, project_id: str, entry: Any, project_root: Path
) -> _ParsedProject:
    if not isinstance(entry, dict):
        raise ProjectImportError(source, f"project '{project_id}' is not a mapping")
    try:
        validate_project_id(project_id)
    except ValueError as exc:
        raise ProjectImportError(source, f"project '{project_id}': {exc}") from exc

    raw_root = entry.get("root")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ProjectImportError(source, f"project '{project_id}' has no usable 'root'")
    root = _resolve_path(raw_root, project_root)

    raw_workspaces = entry.get("workspaces") or {}
    if not isinstance(raw_workspaces, dict):
        raise ProjectImportError(source, f"project '{project_id}': 'workspaces' is not a mapping")
    workspaces: dict[str, Path] = {}
    for adapter, value in raw_workspaces.items():
        if not isinstance(value, str) or not value.strip():
            raise ProjectImportError(
                source, f"project '{project_id}': workspace '{adapter}' has no usable path"
            )
        workspaces[str(adapter)] = _resolve_path(value, root)

    worker = entry.get("worker_agent") or DEFAULT_WORKER_AGENT
    reviewer = entry.get("reviewer_agent") or DEFAULT_REVIEWER_AGENT
    try:
        validate_agents(str(worker), str(reviewer))
    except ValueError as exc:
        # A pinned runtime is never silently substituted, so an unknown one in the
        # import source blocks the import instead of defaulting.
        raise ProjectImportError(source, f"project '{project_id}': {exc}") from exc

    charter = entry.get("charter") or {}
    if not isinstance(charter, dict):
        raise ProjectImportError(source, f"project '{project_id}': 'charter' is not a mapping")

    name = entry.get("name") or project_id
    if not isinstance(name, str):
        raise ProjectImportError(source, f"project '{project_id}': 'name' is not text")

    return _ParsedProject(
        project_id=project_id,
        fields={
            "name": name,
            "root": str(root),
            "workspaces": {k: str(v) for k, v in sorted(workspaces.items())},
            "worker_agent": str(worker),
            "reviewer_agent": str(reviewer),
            "charter": charter,
        },
        root=root,
        workspaces=workspaces,
    )


def import_projects_from_yaml(
    store: ProjectStore,
    path: str | Path,
    *,
    project_root: Path,
    on_project_added=None,
) -> ProjectImportResult:
    """Import the YAML into ``store``. Idempotent, keyed by project_id.

    ``on_project_added`` is called with each newly inserted ``Project`` so the
    caller can create its directories; it is not called for rows that already
    existed.
    """
    document = read_project_yaml(path, project_root=project_root)
    source = str(Path(path))

    imported: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    conflicts: list[ProjectImportConflict] = []

    for parsed in document.projects:
        outcome = store.apply_import(
            parsed.project_id,
            fields=parsed.fields,
            root=parsed.root,
            workspaces=parsed.workspaces,
            imported_from=source,
        )
        if outcome.status == "inserted":
            imported.append(parsed.project_id)
            if on_project_added is not None:
                on_project_added(outcome.project)
        elif outcome.status == "updated":
            updated.append(parsed.project_id)
        elif outcome.status == "unchanged":
            unchanged.append(parsed.project_id)
        else:
            conflicts.append(
                ProjectImportConflict(
                    project_id=parsed.project_id,
                    fields=tuple(_differing_fields(outcome.stored_fields, parsed.fields)),
                )
            )

    # The current-project pointer and the projects root live in the store now.
    # Import seeds them only when the store has no value: a selection made in
    # Agora must survive re-importing the file it was seeded from.
    current = store.current_project_id()
    if current is None and document.current:
        # The parser rejects a 'current' the file does not list, and every listed
        # project has just been inserted or kept, so this names a real row.
        # set_current_project_id fails closed if that ever stops being true.
        store.set_current_project_id(document.current)
        current = document.current

    base = store.get_setting(SETTING_PROJECTS_BASE)
    if base is None and document.projects_base:
        base = str(_resolve_path(document.projects_base, project_root))
        store.set_setting(SETTING_PROJECTS_BASE, base)

    return ProjectImportResult(
        source=source,
        imported=tuple(imported),
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        conflicts=tuple(conflicts),
        current_project_id=current,
        projects_base=base,
    )


def _differing_fields(stored: dict[str, Any], incoming: dict[str, Any]) -> list[str]:
    return sorted(key for key in set(stored) | set(incoming) if stored.get(key) != incoming.get(key))


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


__all__ = [
    "ProjectImportConflict",
    "ProjectImportError",
    "ProjectImportResult",
    "ProjectImportSourceMissing",
    "import_projects_from_yaml",
    "read_project_yaml",
    "SETTING_CURRENT_PROJECT",
]
