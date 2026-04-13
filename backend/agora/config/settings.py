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
        for line in _env_path.read_text().splitlines():
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
    with open(p) as f:
        _config = _resolve_env(yaml.safe_load(f) or {})
    _config_path = p
    return _config


def reset_config():
    global _config, _config_path
    _config = None
    _config_path = None
