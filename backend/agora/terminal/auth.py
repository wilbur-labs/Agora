"""Who may open a terminal: whoever can read this install's token file (CR-30).

A terminal is a shell with the server account's rights. The local-origin guard (CR-34)
keeps web pages out; it does not keep out another account on the same machine, which
can reach 127.0.0.1 directly. So every terminal route needs a secret only the person who
runs Agora can read: a random token in a file under this install's data directory,
readable only by its owner (made so BEFORE the secret is written into it).

The token never travels in a URL — URLs end up in access logs and browser history. The
launcher, which can read the file, trades it (in a request header) for a one-time launch
code good for 60 seconds, and opens the browser at ``/?terminal_code=…``. The page
redeems the code for the token and keeps it in ``localStorage``, which — unlike a cookie —
is scoped to scheme, host AND port: a service on another localhost port never receives it
(review). Terminal requests carry it in a header, the WebSocket in its first message.
"""
from __future__ import annotations

import getpass
import hmac
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

TOKEN_FILE = "terminal-token"
TOKEN_HEADER = "x-agora-terminal-token"
LAUNCH_CODE_SECONDS = 60


def token_path(data_dir: Path) -> Path:
    return Path(data_dir) / TOKEN_FILE


def _restrict_to_owner(path: Path) -> None:
    """Only the account running Agora may read the file. On Windows the directory's
    inherited ACL usually lets other users read; replace it with one grant."""
    if os.name == "nt":
        user = os.environ.get("USERNAME") or getpass.getuser()
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, check=True, timeout=15,
        )
    else:
        os.chmod(path, 0o600)


def load_or_create_token(data_dir: Path) -> str:
    """The install's terminal token, created on first use. The file is created empty,
    exclusively and owner-only, and only then receives the secret (review); creation
    that cannot restrict it fails rather than leave a readable secret."""
    path = token_path(data_dir)
    try:
        text = path.read_text(encoding="ascii").strip()
        if text:
            return text
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        _restrict_to_owner(tmp)
        token = secrets.token_urlsafe(32)
        with open(tmp, "w", encoding="ascii") as handle:
            handle.write(token)
        try:
            os.link(tmp, path)  # never replaces a token another start wrote meanwhile
        except FileExistsError:
            return path.read_text(encoding="ascii").strip()
        except OSError:
            os.replace(tmp, path)
            return token
        return token
    finally:
        tmp.unlink(missing_ok=True)


def rotate_token(data_dir: Path) -> str:
    token_path(data_dir).unlink(missing_ok=True)
    return load_or_create_token(data_dir)


def token_matches(expected: str, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(expected.encode(), str(presented).encode())


class LaunchCodes:
    """One-time codes that stand in for the token in a URL: 60 s, used once."""

    def __init__(self) -> None:
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + LAUNCH_CODE_SECONDS
        return code

    def redeem(self, code: str | None) -> bool:
        if not code:
            return False
        with self._lock:
            expires = self._codes.pop(code, None)
        return expires is not None and expires > time.time()
