"""Configuration loader — reads config.yaml with env var substitution."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import PathProbe, probe_path

# Load .env if present — check project root first, then backend dir
_root = Path(__file__).resolve().parent.parent.parent.parent  # /home/user/Agora
_backend = Path(__file__).resolve().parent.parent.parent       # /home/user/Agora/backend
for _dir in [_root, _backend, Path.cwd()]:
    _env_path = _dir / ".env"
    if _env_path.exists():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
        break

_config: dict | None = None
_config_path: Path | None = None


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def get_config(path: str | Path | None = None) -> dict:
    global _config, _config_path
    if path:
        p = Path(path)
    elif configured_path := os.environ.get("AGORA_CONFIG_PATH", "").strip():
        p = Path(configured_path)
    else:
        # Search: project root → backend dir → cwd
        for d in [_root, _backend, Path.cwd()]:
            candidate = d / "config.yaml"
            if candidate.exists():
                p = candidate
                break
        else:
            p = _root / "config.yaml"
    # Absolute, here, once. `config_dir()` takes `.parent` of this, and a relative
    # name — `get_config("config.yaml")`, or `AGORA_CONFIG_PATH=config.yaml` — leaves
    # a parent of `.`, which resolves against whatever the working directory is at the
    # time it is *asked*. That is the cwd-dependent identity this whole change exists
    # to remove, reintroduced one level up. Resolving before the cache comparison also
    # keeps two spellings of one file from being treated as two configurations.
    p = p.expanduser().resolve()
    if _config is not None and _config_path == p:
        return _config
    with p.open(encoding="utf-8") as f:
        _config = _resolve_env(yaml.safe_load(f) or {})
    _config_path = p
    return _config


def reset_config():
    global _config, _config_path
    _config = None
    _config_path = None


def config_dir() -> Path:
    """The directory the loaded ``config.yaml`` sits in — the installation's root.

    Exactly the paths listed under :func:`resolve_control_plane_db_path` are resolved
    against this — **not** "every relative path in the configuration". That list lives
    there and only there: this docstring used to carry its own copy, and the copy went
    stale twice while the original was being corrected, which is the whole argument for
    not having a second one.

    Where the file is, the installation is; where a process happened to be started is
    not a property of the installation. Before any configuration has been loaded this
    falls back to the repository root, which is also where the search starts.
    """
    # `_config_path` is stored absolute by `get_config`, so this is not sensitive to
    # where it is called from.
    return (_config_path.parent if _config_path is not None else _root).resolve()


def _anchored(raw: str, anchor: Path) -> Path:
    """Expand ``~`` and resolve a relative path against ``anchor``."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else anchor / path


class ControlPlaneDatabaseAmbiguity(RuntimeError):
    """Two candidate databases, and no way to tell which one the operator means."""


def resolve_control_plane_db_path(config: dict, *, anchor: Path | None = None) -> Path:
    """The one SQLite file that holds control-plane state.

    Tasks, runs, gates and projects share this file on purpose: a project and the
    rows that reference it must be writable in a single transaction, which a
    second database file would make impossible. Every caller must resolve it
    here, or the stores can drift onto different files.

    Precedence, extending what the task store has always used:

    1. ``control_plane.db_path``
    2. ``memory.data_dir`` + ``agora.db``
    3. beside ``projects.registry_path`` when no data root is configured — this is
       what keeps configurations that name only a project import file, as the
       tests do, out of the real database
    4. ``data/agora.db``

    There is deliberately **no per-caller base**. Rule 3 used to take a
    ``project_root`` from its caller, and ``ProjectRegistry`` passed one while the
    task store passed none. That was harmless only while both happened to be the
    repository root; anchoring to config.yaml pulled them apart, and two callers in
    one process resolving to two files is precisely the "one SQLite file" invariant
    at the top of this docstring being broken. A caller that needs a different root
    passes ``anchor``, which moves *every* rule together.

Anchored to the directory holding ``config.yaml`` (this function and
    :func:`config_dir`): a relative ``control_plane.db_path``; a relative
    ``memory.data_dir``, both here and for the embeddings vector store; the rule-3
    fallback that places the database *beside* ``projects.registry_path``; and the
    ``data/agora.db`` default. **That is the whole list.** Three other rules resolve the
    rest of the file, none of them this function's business:

    * ``ProjectRegistry.project_root`` (the Agora checkout) — the *import source* that
      ``projects.registry_path`` also names, and each project's ``root``.
    * ``ProjectRegistry.project_root`` again — ``execution.allowed_workspace_roots``,
      anchored independently by three constructors (``execution.router``,
      ``workspaces.router``, ``api.settings_router``). It is not in the shipped file,
      which is how an earlier version of this list missed it: the set of keys was
      enumerated from ``config.yaml`` rather than from the code that reads it.
    * **Each project's own resolved root** — that project's ``workspaces``.
      ``importer._parse_project`` resolves the root first and the workspaces against it.
    * Neither, for ``research.artifact_dir`` / ``research.workspaces``: with a
      ``projects`` key present — the shipped configuration has one —
      ``ResearchOrchestrator`` *overwrites* both from the current project, so the values
      in the file are ignored rather than resolved. Only a configuration without
      ``projects`` leaves them relative to the process.

    ``projects.registry_path`` appears in the first list **and** the second. One key,
    two consumers: this function anchors it to place a database next to it, and the
    registry opens the file itself relative to its own root. Assigning the key wholesale
    to either rule is false, as three earlier attempts at this paragraph did — each
    checked against part of the code and stated about all of it. ``anchor`` overrides the base, for tests.

    It used to be
    anchored to the working directory, which made the identity of the whole control
    plane depend on where the process was started: ``make dev`` from the repository
    root and from ``backend/`` were two different databases, two different project
    lists and two different task lists, with nothing on screen to say so. Changing
    this without warning would silently move an installation onto its *other*
    database, so it is paired with :func:`assert_no_database_ambiguity`, which the
    API calls before it touches anything.
    """
    anchor = (Path(anchor).expanduser().resolve() if anchor is not None else config_dir())
    explicit = (config.get("control_plane") or {}).get("db_path")
    if explicit:
        return _anchored(explicit, anchor)
    data_dir = (config.get("memory") or {}).get("data_dir")
    if data_dir:
        return _anchored(data_dir, anchor) / "agora.db"
    registry_path = (config.get("projects") or {}).get("registry_path")
    if registry_path:
        path = Path(registry_path).expanduser()
        if not path.is_absolute():
            path = anchor / path
        return path.resolve().parent / "agora.db"
    return anchor / "data" / "agora.db"


def resolve_legacy_cwd_db_path(config: dict) -> Path:
    """Where :func:`resolve_control_plane_db_path` *used* to resolve to.

    Exists only so the ambiguity check can name the file an operator's data may
    still be sitting in. Nothing may read or write through this — it is the answer
    to "what did the old rule say", not to "which database is ours".

    The four rules are spelled out again rather than expressed as "the current
    resolver with a different anchor", for two reasons. Rule 3 was never
    working-directory relative — it used the repository root — so one anchor cannot
    describe the old behaviour. And this is a frozen historical record: when the
    live rules change again, this one must *not* follow, or the check silently
    stops comparing against what is actually on disk.

    The old rules produced a *relative* path, which sqlite then resolved against
    the working directory when it opened the file. Returning it absolute here says
    the same thing in the only form a diagnostic can print.
    """
    return _absolute_against_cwd(_legacy_rules(config))


def _absolute_against_cwd(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _legacy_rules(config: dict) -> Path:
    explicit = (config.get("control_plane") or {}).get("db_path")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = (config.get("memory") or {}).get("data_dir")
    if data_dir:
        return Path(data_dir).expanduser() / "agora.db"
    registry_path = (config.get("projects") or {}).get("registry_path")
    if registry_path:
        # Rule 3 was never working-directory relative: it used the repository root,
        # and every caller that passed a ``project_root`` passed that same root.
        path = Path(registry_path).expanduser()
        if not path.is_absolute():
            path = _root / path
        return path.resolve().parent / "agora.db"
    return Path("./data") / "agora.db"


def _link_target_unreadable(probe: PathProbe) -> bool:
    """A symlink at the path whose target could not be stat'd.

    :func:`probe_path` reports this as ``other`` with an error rather than as
    ``unknown``, because *something* is definitely recorded at the path. For the
    question this module asks — is there a database sitting there — that is not
    settled: the link may well point at one. Only a ``FileNotFoundError`` on the
    target settles it, and it settles it as absence, which is fsprobe's own rule
    ("only FileNotFoundError is absence") applied one level down, to the target
    instead of the name.

    Without this, `kind != "file"` read a symlink-to-an-unreadable-database as
    "nothing to worry about" and started on the other database — the exact silent
    switch the check exists to prevent, one representation away from where it was
    being looked for.
    """
    return (
        probe.kind == "other"
        and probe.error is not None
        and not isinstance(probe.error, FileNotFoundError)
    )


def _same_file(left: Path, right: Path) -> bool:
    """True when both names lead to the same file.

    Three passes, weakest claim last. ``normpath`` first, so ``<root>/data`` and
    ``<root>/backend/../data`` are not reported as two databases — it is textual and
    touches no disk. Then ``samefile``, which is the only thing that sees through a
    junction or symlink, and which needs both files to exist. When one does not,
    the names are all that is left; that is a weaker answer, and it errs toward
    "different", which raises the check rather than silencing it.
    """
    left = Path(os.path.normpath(left))
    right = Path(os.path.normpath(right))
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def assert_no_database_ambiguity(config: dict, *, anchor: Path | None = None) -> Path:
    """Refuse to start when two different databases both have a claim. Returns the
    one that wins when there is no doubt.

    The anchoring rule changed under installations that already exist, so on the
    first start after the change a process can resolve to a *different* file than
    the one it has been writing to. Switching silently is the failure this exists to
    prevent: the operator sees an emptier — or simply other — project and task list
    and concludes their data is gone.

    The condition is deliberately wider than "the new path has no database yet".
    That narrower check was the first thing written down, and on the machine it was
    written for it would never have fired: **both** paths held a database, of
    different sizes and different ages. A check narrower than the claim it makes is
    worse than none, because it reads as proof.

    So: if the old working-directory rule names a *different file* and that file
    exists, refuse and print both absolute paths — whether or not the anchored path
    exists too. The operator decides; an automatic pick would be the silent switch
    under a more confident name.

    The way out is to say which one is meant, in the configuration:
    ``control_plane.db_path: <absolute path>`` resolves identically under both
    rules, so it ends the ambiguity by answering it.
    """
    anchored = resolve_control_plane_db_path(config, anchor=anchor)
    legacy = resolve_legacy_cwd_db_path(config)
    if _same_file(anchored, legacy):
        return anchored
    old = probe_path(legacy)
    if old.unknown or _link_target_unreadable(old):
        # Not absence. Whether a database is sitting there was not established, so
        # the one thing that must not happen next is proceeding as though it is not.
        # This is a stat of a path derived from the operator's own configuration; a
        # process that cannot perform it is in a state worth stopping for, and the
        # way out is the same one sentence of configuration.
        raise ControlPlaneDatabaseAmbiguity(
            "Could not tell whether an older control-plane database is still in "
            f"place, so this process will not assume it is not ({old.reason()}).\n"
            f"  would use (anchored to config.yaml): {anchored}\n"
            f"  could not be read (the old rule, relative to the working directory): {legacy}\n"
            "Set an absolute control_plane.db_path in config.yaml to say which "
            "database is meant, or make that path readable."
        )
    if old.kind != "file":
        # What is left here is established: a directory named `agora.db`, a device
        # node, or a symlink whose target is definitely gone. None of those is a
        # database, the old rule could never have opened one as such, and there is
        # therefore no data at risk -- which is the only thing this check exists to
        # protect. Refusing to start over one would be a guard wider than its reason.
        return anchored
    raise ControlPlaneDatabaseAmbiguity(
        "Two candidate control-plane databases, and this process will not guess "
        "which one is yours.\n"
        f"  would use (anchored to config.yaml): {anchored}\n"
        f"  also exists (the old rule, relative to the working directory): {legacy}\n"
        "Relative paths in config.yaml are now resolved against the directory "
        "holding config.yaml, not the directory the process was started in.\n"
        "Move the database you want to the first path, or set an absolute "
        "control_plane.db_path in config.yaml to say which one is meant."
    )
