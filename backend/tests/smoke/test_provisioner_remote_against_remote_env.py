"""Cross-unit smoke: U3 (remote-worktree) against U2 (remote-env)'s REAL AsyncFilesystem.

Per .kiro/steering/cross-unit-smoke.md: exercise U3's real generated code against U2's
REAL implementation — never mock the upstream unit. Here that means the provisioner's
confinement check runs through the ACTUAL ``RemoteEnvironment.fs`` (a real
``RemoteFilesystem``), whose ``realpath`` walks the path component-by-component over
SFTP. Only the asyncssh SFTP transport underneath is faked (no real host in unit CI);
U2's ``RemoteFilesystem`` and U3's ``RemoteWorkspaceProvisioner._assert_allowed`` are
the real, un-mocked things under test.

Litmus (cross-unit-smoke.md): if U2's ``env.fs`` accessor or its realpath's
escaping-symlink defense were broken, this must fail. Run with --noconftest.
"""
from __future__ import annotations

import asyncio
import stat

import asyncssh
import pytest

from agora.environments_remote import RemoteEnvironment, RemoteFilesystem
from agora.workspaces.models import ProvisionRequest
from agora.workspaces.provisioner import WorkspaceValidationError
from agora.workspaces.provisioner_remote import RemoteWorkspaceProvisioner


def _run(coro):
    return asyncio.run(coro)


class _Attrs:
    def __init__(self, mode):
        self.permissions = mode


DIR = stat.S_IFDIR | 0o755
LNK = stat.S_IFLNK | 0o777


class _FakeSFTP:
    """A tiny SFTP over a dict filesystem: path -> 'dir' | 'link:<target>'. Missing
    path raises SFTPNoSuchFile (definite ENOENT). This is the ONLY faked layer."""
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
        return _Attrs(LNK if str(v).startswith("link:") else DIR)

    async def readlink(self, path):
        return self._fs[path].split("link:", 1)[1]


class _FakeConn:
    def __init__(self, fs):
        self._fs = fs

    def start_sftp_client(self):
        return _FakeSFTP(self._fs)


class _FakeProject:
    def __init__(self, project_id, root, workspaces):
        self.project_id = project_id
        self.root = root
        self.workspaces = workspaces


class _FakeRegistry:
    def __init__(self, project):
        self._p = project

    def get(self, _id):
        return self._p


def _real_remote_fs(monkeypatch, fs_map):
    """A REAL RemoteEnvironment whose only fake is the asyncssh connection."""
    monkeypatch.setenv("AGORA_SSH_KEY_x", "pw")
    env = RemoteEnvironment(host="h", user="u", secret_ref="AGORA_SSH_KEY_x")
    conn = _FakeConn(fs_map)

    async def _fake_ensure():
        return conn

    monkeypatch.setattr(env, "_ensure_conn", _fake_ensure)
    return env


def test_env_fs_is_a_real_async_filesystem(monkeypatch):
    # U3 reaches U2's filesystem through env.fs. That accessor must exist and hand back
    # the real RemoteFilesystem exposing the AsyncFilesystem surface — if U2
    # renamed/removed it, U3 cannot plug in. (AsyncFilesystem is deliberately NOT
    # @runtime_checkable — same reason as AsyncExecutionEnvironment — so we check the
    # concrete type and the six-method surface rather than isinstance the Protocol.)
    env = _real_remote_fs(monkeypatch, {"/": "dir"})
    fs = env.fs
    assert isinstance(fs, RemoteFilesystem)
    for method in ("realpath", "is_empty_dir", "read_file", "write_file", "mkdir", "rmdir"):
        assert asyncio.iscoroutinefunction(getattr(fs, method)), method


def test_provisioner_confinement_uses_real_realpath_and_reveals_escape(monkeypatch):
    # The workspace path contains a symlink that escapes the project root. Driven
    # through U2's REAL realpath (not a fake), _assert_allowed must SEE the escape and
    # refuse — the exact defense Step 1 built. If realpath masked it (the bug the
    # rewrite fixed), confinement would wrongly pass.
    project_root = "/home/u/proj"
    # /home/u/proj/ws is a symlink pointing outside the root.
    ws = "/home/u/proj/ws"
    fs_map = {
        "/": "dir", "/home": "dir", "/home/u": "dir", "/home/u/proj": "dir",
        "/home/u/proj/ws": "link:/etc/evil", "/etc": "dir", "/etc/evil": "dir",
    }
    env = _real_remote_fs(monkeypatch, fs_map)
    project = _FakeProject("proj", project_root, {"codex": ws})
    prov = RemoteWorkspaceProvisioner(
        _FakeRegistry(project), env, env.fs, remote_project_root=project_root
    )

    async def scenario():
        await prov._assert_allowed(project, prov._remote_project_root.__class__(ws))

    with pytest.raises(WorkspaceValidationError, match="outside"):
        _run(scenario())


def test_provisioner_confinement_allows_in_root_path(monkeypatch):
    # A workspace genuinely inside the root (no escaping link) must pass the same real
    # realpath path — proving the smoke fails for the right reason, not always.
    fs_map = {
        "/": "dir", "/home": "dir", "/home/u": "dir", "/home/u/proj": "dir",
        "/home/u/proj/.agora": "dir", "/home/u/proj/.agora/ws": "dir",
    }
    env = _real_remote_fs(monkeypatch, fs_map)
    ws = "/home/u/proj/.agora/ws"
    project = _FakeProject("proj", "/home/u/proj", {"codex": ws})
    prov = RemoteWorkspaceProvisioner(
        _FakeRegistry(project), env, env.fs, remote_project_root="/home/u/proj"
    )

    async def scenario():
        # Must NOT raise.
        await prov._assert_allowed(project, prov._remote_project_root.__class__(ws))

    _run(scenario())
