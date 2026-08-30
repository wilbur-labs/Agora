"""Configuration loader — reads config.yaml with env var substitution."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

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


def resolve_control_plane_db_path(config: dict, *, project_root: Path | None = None) -> Path:
    """The one SQLite file that holds control-plane state.

    Tasks, runs, gates and projects share this file on purpose: a project and the
    rows that reference it must be writable in a single transaction, which a
    second database file would make impossible. Every caller must resolve it
    here, or the stores can drift onto different files.

    Precedence, extending what the task store has always used:

    1. ``control_plane.db_path``
    2. ``memory.data_dir`` + ``agora.db`` (a relative data_dir stays relative to
       the working directory, as before)
    3. beside ``projects.registry_path`` when no data root is configured — this is
       what keeps configurations that name only a project import file, as the
       tests do, out of the real database
    4. ``./data/agora.db``

    Consequences this change deepened (accepted, not fixed here)
    -----------------------------------------------------------
    Two pre-existing problems got worse when projects moved into this file. They
    are recorded so the next slice sees the cost instead of rediscovering it:

    * Rules 2 and 4 are relative to the *working directory*, so which database a
      process uses depends on where it was started. That already decided where
      tasks, runs and gates live; it now also decides where projects and the
      current-project pointer live, so starting the server from another directory
      can present a different project list, not just a different task list.
    * ``ProjectRegistry`` imports the YAML when it is constructed and the API's
      startup path constructs one, so serving the API now writes project state
      into whichever file this resolves to — the same live file a test process
      writes to when it resolves the same way. Rule 3 keeps configurations that
      name only an import file away from the real database, which mitigates the
      working-directory default for those callers but does not fix it.
    """
    explicit = (config.get("control_plane") or {}).get("db_path")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = (config.get("memory") or {}).get("data_dir")
    if data_dir:
        return Path(data_dir).expanduser() / "agora.db"
    registry_path = (config.get("projects") or {}).get("registry_path")
    if registry_path:
        base = Path(project_root).resolve() if project_root else _root
        path = Path(registry_path).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve().parent / "agora.db"
    return Path("./data") / "agora.db"
