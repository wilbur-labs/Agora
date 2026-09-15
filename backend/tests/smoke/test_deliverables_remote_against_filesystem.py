"""Cross-unit smoke: U4 (remote-deliverables) against U3 (AsyncFilesystem)'s REAL impl.

Per .kiro/steering/cross-unit-smoke.md: U4's read_file runs through the ACTUAL
RemoteFilesystem (U3/U2), not a fake — only the asyncssh SFTP transport underneath is
stubbed. U4 consumes U3's AsyncFilesystem.read_file (bounded, typed-error) + realpath
(remote confinement); this proves the plug is real.

Litmus: if U3's read_file bound or realpath confinement were broken, this must fail.
Run with --noconftest.
"""
from __future__ import annotations

import asyncio
import stat

import asyncssh
import pytest

from agora.environments_remote import RemoteEnvironment
from agora.execution.deliverables_remote import RemoteDeliverables


def _run(coro):
    return asyncio.run(coro)


class _Attrs:
    def __init__(self, mode, size=None):
        self.permissions = mode
        self.size = size


DIR = stat.S_IFDIR | 0o755
REG = stat.S_IFREG | 0o644


class _FakeFile:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeSFTP:
    """A dict filesystem: path -> ('dir' | ('file', bytes)). Missing -> SFTPNoSuchFile."""
    def __init__(self, fs):
        self._fs = fs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def lstat(self, path, flags=0):
        v = self._fs.get(path)
        if v is None:
            raise asyncssh.SFTPNoSuchFile("no such file")
        return _Attrs(DIR if v == "dir" else REG)

    async def stat(self, path):
        v = self._fs.get(path)
        if v is None:
            raise asyncssh.SFTPNoSuchFile("no such file")
        if v == "dir":
            return _Attrs(DIR)
        return _Attrs(REG, size=len(v[1]))

    def open(self, path, mode):
        v = self._fs.get(path)
        if v is None or v == "dir":
            raise asyncssh.SFTPNoSuchFile("no such file")
        return _FakeFile(v[1])


class _FakeConn:
    def __init__(self, fs):
        self._fs = fs

    def start_sftp_client(self):
        return _FakeSFTP(self._fs)


def _real_env(monkeypatch, fs_map):
    monkeypatch.setenv("AGORA_SSH_KEY_x", "pw")
    env = RemoteEnvironment(host="h", user="u", secret_ref="AGORA_SSH_KEY_x")
    conn = _FakeConn(fs_map)

    async def _fake_ensure():
        return conn

    monkeypatch.setattr(env, "_ensure_conn", _fake_ensure)
    return env


WS = "/home/u/proj/.agora/workspaces/codex"


def test_read_file_through_real_remote_filesystem(monkeypatch):
    from pathlib import PurePosixPath

    target = f"{WS}/out.txt"
    fs_map = {
        "/": "dir", "/home": "dir", "/home/u": "dir", "/home/u/proj": "dir",
        "/home/u/proj/.agora": "dir", "/home/u/proj/.agora/workspaces": "dir",
        WS: "dir", target: ("file", b"api_key=SECRETXYZ99999\nhello"),
    }
    env = _real_env(monkeypatch, fs_map)
    d = RemoteDeliverables(env, env.fs)  # U3's REAL AsyncFilesystem
    result = _run(d.read_file(PurePosixPath(WS), "out.txt"))
    assert result["path"] == "out.txt"
    assert "[REDACTED]" in result["content"]        # NFR-4 redaction applied
    assert "SECRETXYZ99999" not in result["content"]
    assert result["truncated"] is False


def test_read_file_escape_revealed_by_real_realpath(monkeypatch):
    # A symlink inside the workspace escaping outside must be REVEALED by U3's real
    # realpath and rejected by U4's confinement — not masked.
    from pathlib import PurePosixPath

    fs_map = {
        "/": "dir", "/home": "dir", "/home/u": "dir", "/home/u/proj": "dir",
        "/home/u/proj/.agora": "dir", "/home/u/proj/.agora/workspaces": "dir",
        WS: "dir",
        f"{WS}/leak": "dir",  # placeholder; the escape is via a link component below
    }
    # Make /home/u/proj/.agora/workspaces/codex/leak a symlink to /etc via lstat mode.
    class _LinkSFTP(_FakeSFTP):
        async def lstat(self, path, flags=0):
            if path == f"{WS}/leak":
                return _Attrs(stat.S_IFLNK | 0o777)
            return await super().lstat(path, flags)
        async def readlink(self, path):
            return "/etc"
    env = _real_env(monkeypatch, {**fs_map, "/etc": "dir"})
    monkeypatch.setattr(_FakeConn, "start_sftp_client", lambda self: _LinkSFTP(self._fs))
    d = RemoteDeliverables(env, env.fs)
    with pytest.raises(PermissionError, match="escapes"):
        _run(d.read_file(PurePosixPath(WS), "leak/passwd"))
