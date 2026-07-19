"""Fail-closed bearer authentication for the Control Plane API."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agora.config.settings import get_config


@dataclass(frozen=True)
class ControlPrincipal:
    principal_id: str
    permissions: frozenset[str]
    projects: frozenset[str]


_bearer = HTTPBearer(auto_error=False)
_principal_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_project_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_permissions = frozenset(
    {
        "control_plane.read",
        "control_plane.register",
        "control_plane.evaluate",
        "control_plane.approve",
    }
)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or missing bearer credential",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_control_plane(
    credential: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ControlPrincipal:
    if credential is None or credential.scheme.lower() != "bearer":
        raise _unauthorized()
    token = credential.credentials
    if not token or len(token.encode("utf-8")) > 4096:
        raise _unauthorized()
    supplied = hashlib.sha256(token.encode("utf-8")).digest()
    entries = (
        get_config().get("control_plane", {}).get("auth", {}).get("credentials", [])
    )
    match: ControlPrincipal | None = None
    match_count = 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        secret_ref = entry.get("secret_ref")
        principal_id = entry.get("principal")
        secret = os.environ.get(secret_ref, "") if isinstance(secret_ref, str) else ""
        if (
            not secret
            or not isinstance(principal_id, str)
            or _principal_id.fullmatch(principal_id) is None
        ):
            continue
        expected = hashlib.sha256(secret.encode("utf-8")).digest()
        if hmac.compare_digest(supplied, expected):
            permissions = entry.get("permissions", [])
            projects = entry.get("projects", [])
            if (
                not isinstance(permissions, list)
                or not isinstance(projects, list)
                or any(not isinstance(item, str) for item in permissions)
                or any(not isinstance(item, str) for item in projects)
            ):
                continue
            permission_set = frozenset(permissions)
            project_set = frozenset(projects)
            if not permission_set.issubset(_permissions) or any(
                _project_id.fullmatch(project) is None for project in project_set
            ):
                continue
            match_count += 1
            match = ControlPrincipal(
                principal_id=principal_id,
                permissions=permission_set,
                projects=project_set,
            )
    if match is None or match_count != 1:
        raise _unauthorized()
    return match


def authorize(principal: ControlPrincipal, project_id: str, permission: str) -> None:
    if permission not in principal.permissions or project_id not in principal.projects:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permission")
