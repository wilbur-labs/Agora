"""Installing the session reporter (CR-75 §3).

``<data>/work-hooks/`` holds a copy of ``report_hook.py`` and a ``settings.json`` with a
``SessionStart`` and a ``SessionEnd`` hook; a work-page claude gets ``--settings`` with
that file, and nothing else on the machine does. ``<data>/work-sessions/<key>/`` holds
the journals. Both directories are restricted to the account running Agora; when that
cannot be done, or a path would need quoting to survive the hook's shell, there is no
hook, and the page says the conversation cannot be recorded — it does not run anyway.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: What a path in the hook command may contain. The command is run by a shell Agora does
#: not choose (Git Bash on Windows); inside double quotes these are literal in every one.
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9 :/._-]+$")


@dataclass
class HookSetup:
    settings: Path | None
    sessions_dir: Path | None
    #: Why there is no hook, when ``settings`` is None.
    reason: str | None
    #: Where the status-line relay records claude's usage (CR-87).
    usage_dir: Path | None = None


#: The status line a work-page claude gets (CR-87): the relay records ``rate_limits`` and
#: passes the document on to the workspace's own status line, which the launch names in
#: ``AGORA_STATUSLINE_COMMAND`` (a ``--settings`` status line replaces the workspace's, so
#: it is run from here). Empty: no line, as before there was one.
def _statusline_command(python_path: str, relay_path: str) -> str:
    return f'"{python_path}" "{relay_path}" | bash -c "$AGORA_STATUSLINE_COMMAND"'


def _restrict_dir(path: Path) -> None:
    """Only the account running Agora may enter the directory (``terminal/auth.py``'s rule,
    for a directory: inheritance removed, one grant inherited by what is created inside) —
    and then checked: ``/grant:r`` replaces only this account's entry, so an explicit grant
    to anyone else that was already there would survive it (kiro review)."""
    if os.name == "nt":
        name = os.environ.get("USERNAME") or getpass.getuser()
        domain = os.environ.get("USERDOMAIN")
        # Qualified: a local and a domain account may share the bare name (codex review).
        user = f"{domain}\\{name}" if domain else name
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
            capture_output=True, check=True, timeout=15,
        )
        listing = subprocess.run(["icacls", str(path)], capture_output=True, check=True, timeout=15)
        others = foreign_grants(listing.stdout.decode("mbcs", errors="replace"), str(path), user)
        if others:
            raise PermissionError(f"other accounts still have access: {', '.join(others)}")
    else:
        os.chmod(path, 0o700)


def foreign_grants(listing: str, path: str, user: str) -> list[str]:
    """The principals in an ``icacls <path>`` listing other than ``user``. Every entry line
    is ``<principal>:(<rights>)``; the summary line at the end has no such shape.

    ``user`` is compared whole, domain included when it has one: ``MACHINE\\alice`` and
    ``DOMAIN\\alice`` are two accounts. Only an unqualified ``user`` matches by bare name."""
    found: list[str] = []
    for line in listing.splitlines():
        text = line.strip()
        if text.lower().startswith(path.lower()):
            text = text[len(path):].strip()
        if ":(" not in text:
            continue
        principal = text.split(":(", 1)[0].strip()
        name = principal if "\\" in user else principal.rsplit("\\", 1)[-1]
        if name.lower() != user.lower() and principal not in found:
            found.append(principal)
    return found


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


def install(data_dir: Path, python: str | None = None) -> HookSetup:
    python = python or sys.executable
    hooks_dir, sessions_dir = Path(data_dir) / "work-hooks", Path(data_dir) / "work-sessions"
    usage_dir = Path(data_dir) / "work-usage"
    try:
        for folder in (hooks_dir, sessions_dir, usage_dir):
            folder.mkdir(parents=True, exist_ok=True)
            refuse_link(folder)
            _restrict_dir(folder)
    except LinkRefused as exc:
        return HookSetup(None, None, str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        return HookSetup(None, None, f"could not restrict the work data directories to this account: {type(exc).__name__}")
    script = hooks_dir / "report_hook.py"
    python_path, script_path = Path(python).resolve().as_posix(), _posix(script)
    for label, value in (("Python", python_path), ("data directory", script_path)):
        if not SAFE_PATH_RE.fullmatch(value):
            return HookSetup(None, None, f"the {label} path has characters the hook's shell would interpret: {value}")
    command = f'"{python_path}" "{script_path}"'
    relay = hooks_dir / "statusline_relay.py"
    settings = {"hooks": {name: [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
                          for name in ("SessionStart", "SessionEnd")},
                "statusLine": {"type": "command", "command": _statusline_command(python_path, _posix(relay))}}
    try:
        source = (Path(__file__).parent / "report_hook.py").read_bytes()
        relay_source = (Path(__file__).parent / "statusline_relay.py").read_bytes()
        for target in (script, relay, hooks_dir / "settings.json"):
            refuse_link(target)
        _write_if_changed(script, source)
        _write_if_changed(relay, relay_source)
        _write_if_changed(hooks_dir / "settings.json", json.dumps(settings, indent=2).encode("utf-8"))
    except OSError as exc:
        return HookSetup(None, None, f"could not write the session reporter: {type(exc).__name__}")
    return HookSetup(hooks_dir / "settings.json", sessions_dir, None, usage_dir)


def _write_if_changed(path: Path, content: bytes) -> None:
    try:
        if path.read_bytes() == content:
            return
    except OSError:
        pass
    # A new name, created exclusively: an existing file or link at a fixed ``.tmp`` name
    # would otherwise be written through (kiro review round 7).
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        refuse_link(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def journal_dir(sessions_dir: Path, key: str) -> Path:
    folder = sessions_dir / key
    folder.mkdir(exist_ok=True)
    refuse_link(folder)
    return folder


class LinkRefused(OSError):
    pass


def refuse_link(path: Path) -> None:
    """Agora writes and deletes here; through a symbolic link or a junction those would
    land somewhere else (kiro review). A path that is one is refused, not followed."""
    if path.is_symlink() or _is_reparse_point(path):
        raise LinkRefused(f"refusing a link in Agora's work data: {path}")


#: Windows' mark on a junction, a symbolic link and every other redirecting entry.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(path: Path) -> bool:
    """A junction, read from the entry's own attributes: ``os.path.isjunction`` exists only
    from Python 3.12, and the supported range starts lower (kiro review round 8)."""
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def still_installed(setup: HookSetup) -> bool:
    """Whether what ``install`` wrote is still there (an antivirus may quarantine it)."""
    if setup.settings is None or setup.sessions_dir is None:
        return False
    try:
        for path in (setup.settings.parent, setup.settings, setup.settings.parent / "report_hook.py",
                     setup.settings.parent / "statusline_relay.py", setup.sessions_dir):
            refuse_link(path)
        if not setup.sessions_dir.is_dir():
            return False
        # The content too: a file emptied or cut short is not the hook (kiro review round 9).
        for name in ("report_hook.py", "statusline_relay.py"):
            if (setup.settings.parent / name).read_bytes() != (Path(__file__).parent / name).read_bytes():
                return False
        hooks = json.loads(setup.settings.read_bytes()).get("hooks", {})
        script = _posix(setup.settings.parent / "report_hook.py")
        return all(
            any(script in str(hook.get("command", "")) for entry in hooks.get(name, []) for hook in entry.get("hooks", []))
            for name in ("SessionStart", "SessionEnd")
        )
    except (OSError, ValueError, AttributeError):
        return False
