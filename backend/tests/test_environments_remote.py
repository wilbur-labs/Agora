"""RemoteEnvironment over a fake asyncssh — the logic, without a real host.

The suite has no real SSH server, so these drive RemoteEnvironment against small
hand-written asyncssh stubs (no unittest.mock: explicit fakes are more readable and
let us assert exactly what crossed the wire). They verify the SHAPE of the behavior —
run outcome mapping, probe's absent-vs-unknown via SFTP errors, launch's env allowlist,
host-key-mode validation. Real transport behavior (signals, real known_hosts) is left
to the integration test, which reads its target host from the environment and is marked
`integration` (skipped by default).

Driven with asyncio.run, like test_dispatch / test_environments.
"""
from __future__ import annotations

import asyncio
import os
import stat

import asyncssh
import pytest

from agora.environments import CommandResult
from agora.environments_remote import RemoteEnvironment, RemoteProcessHandle


def _run(coro):
    return asyncio.run(coro)


# --- fake asyncssh building blocks ---

class _FakeCompleted:
    def __init__(self, exit_status, stdout=b"", stderr=b""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class _FakeAttrs:
    def __init__(self, permissions, size=0):
        self.permissions = permissions
        self.size = size


class _FakeSFTP:
    """Context-manager SFTP client. `entries` maps path -> _FakeAttrs (lstat view);
    `targets` maps a symlink path -> target _FakeAttrs (stat view). Missing path raises
    SFTPNoSuchFile; a path in `denied` raises SFTPPermissionDenied."""
    def __init__(self, entries, targets=None, denied=()):
        self._entries, self._targets, self._denied = entries, targets or {}, set(denied)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def lstat(self, path):
        if path in self._denied:
            raise asyncssh.SFTPPermissionDenied("denied")
        if path not in self._entries:
            raise asyncssh.SFTPNoSuchFile("no such file")
        return self._entries[path]

    async def stat(self, path):
        if path in self._denied:
            raise asyncssh.SFTPPermissionDenied("denied")
        if path in self._targets:
            return self._targets[path]
        if path in self._entries and not stat.S_ISLNK(self._entries[path].permissions):
            return self._entries[path]
        # a symlink whose target is absent
        raise asyncssh.SFTPNoSuchFile("target gone")


class _FakeConn:
    def __init__(self, *, run_result=None, run_error=None, sftp=None, captured_env=None):
        self._run_result, self._run_error = run_result, run_error
        self._sftp, self._captured_env = sftp, captured_env

    async def run(self, cmd, *, encoding=None, check=False):
        if self._run_error is not None:
            raise self._run_error
        return self._run_result

    def start_sftp_client(self):
        return self._sftp

    async def create_process(self, cmd, *, encoding=None, env=None):
        if self._captured_env is not None:
            self._captured_env.update({"__env__": env})
        return _FakeProc()

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _FakeProc:
    returncode = 0
    stdout = None
    stderr = None
    async def wait(self):
        return 0
    def terminate(self):
        pass
    def kill(self):
        pass


def _env(monkeypatch, conn, *, secret_ref="AGORA_SSH_KEY_x", secret_val=None):
    """Build a RemoteEnvironment whose _ensure_conn yields `conn`, with the credential
    env var present so _credentials() passes."""
    monkeypatch.setenv(secret_ref, secret_val or "some-password")
    env = RemoteEnvironment(host="h", user="u", secret_ref=secret_ref)
    async def _fake_ensure():
        return conn
    monkeypatch.setattr(env, "_ensure_conn", _fake_ensure)
    return env


# --- host_key_mode validation ---

def test_invalid_host_key_mode_is_refused():
    with pytest.raises(ValueError):
        RemoteEnvironment(host="h", user="u", secret_ref="R", host_key_mode="trustme")


def test_tofu_fails_closed_not_silently_strict(monkeypatch):
    monkeypatch.setenv("R", "pw")
    env = RemoteEnvironment(host="h", user="u", secret_ref="R", host_key_mode="tofu")
    # _known_hosts_arg must refuse rather than behave as strict.
    with pytest.raises(NotImplementedError):
        env._known_hosts_arg()


def test_strict_known_hosts_defaults_to_empty_tuple_and_preserves_path(monkeypatch):
    monkeypatch.setenv("R", "pw")
    # default: () meaning "use asyncssh's default known_hosts files" (strict), never None
    env = RemoteEnvironment(host="h", user="u", secret_ref="R")
    assert env._known_hosts_arg() == ()
    # explicit path is preserved verbatim
    env2 = RemoteEnvironment(host="h", user="u", secret_ref="R", known_hosts_path="/etc/kh")
    assert env2._known_hosts_arg() == "/etc/kh"


# --- run outcome mapping ---

def test_run_completed_maps_exit_and_decodes_utf8(monkeypatch):
    conn = _FakeConn(run_result=_FakeCompleted(0, "日本🧭".encode("utf-8"), b""))
    env = _env(monkeypatch, conn)
    r = _run(env.run(["echo"], timeout=10))
    assert r.outcome == "completed" and r.returncode == 0
    assert r.stdout == "日本🧭"  # bytes -> UTF-8 by us


def test_run_transport_error_is_unstartable_and_not_retried_by_default(monkeypatch):
    calls = {"n": 0}
    class Conn(_FakeConn):
        async def run(self, cmd, *, encoding=None, check=False):
            calls["n"] += 1
            raise asyncssh.ConnectionLost("boom")
    env = _env(monkeypatch, Conn())
    r = _run(env.run(["git", "commit"], timeout=10))  # side-effecting: read_only False
    assert r.outcome == "unstartable"
    assert calls["n"] == 1  # NOT retried — a side-effecting run must not run twice


def test_run_read_only_retries_transport_error(monkeypatch):
    calls = {"n": 0}
    class Conn(_FakeConn):
        async def run(self, cmd, *, encoding=None, check=False):
            calls["n"] += 1
            raise asyncssh.ConnectionLost("boom")
    env = _env(monkeypatch, Conn())
    _run(env.run(["git", "status"], timeout=10, read_only=True))
    assert calls["n"] > 1  # read-only -> retried


# --- probe: absent vs unknown vs kinds ---

def test_probe_absent_only_on_no_such_file(monkeypatch):
    env = _env(monkeypatch, _FakeConn(sftp=_FakeSFTP(entries={})))
    r = _run(env.probe(__import__("pathlib").Path("/gone")))
    assert r.absent


def test_probe_permission_denied_is_unknown_not_absent(monkeypatch):
    p = "/secret"
    env = _env(monkeypatch, _FakeConn(sftp=_FakeSFTP(entries={}, denied={p})))
    r = _run(env.probe(__import__("pathlib").Path(p)))
    assert r.unknown and not r.absent


def test_probe_dir_and_file_with_size(monkeypatch):
    from pathlib import Path
    entries = {
        "/d": _FakeAttrs(stat.S_IFDIR | 0o755),
        "/f": _FakeAttrs(stat.S_IFREG | 0o644, size=123),
    }
    env = _env(monkeypatch, _FakeConn(sftp=_FakeSFTP(entries=entries)))
    d = _run(env.probe(Path("/d"))); assert d.is_dir()
    f = _run(env.probe(Path("/f"))); assert f.is_file() and f.size == 123


def test_probe_dangling_symlink_follow_is_other_not_absent(monkeypatch):
    from pathlib import Path
    entries = {"/link": _FakeAttrs(stat.S_IFLNK | 0o777)}  # lstat sees a symlink
    env = _env(monkeypatch, _FakeConn(sftp=_FakeSFTP(entries=entries, targets={})))
    r = _run(env.probe(Path("/link"), follow_symlinks=True))
    assert not r.absent and not r.is_dir() and not r.is_file()  # present, unresolvable
    assert r.kind == "other"  # link exists, target gone -> other, NOT absent/dir/file


# --- launch env allowlist (credential must never cross) ---

def test_launch_strips_credential_and_non_agora_env(monkeypatch):
    captured = {}
    env = _env(monkeypatch, _FakeConn(captured_env=captured), secret_ref="AGORA_SSH_KEY_x")
    _run(env.launch(
        ["agent"],
        env={
            "AGORA_TASK_ID": "t", "AGORA_RUN_ID": "r", "AGORA_PROJECT_ID": "p",
            "AGORA_SSH_KEY_x": "SECRET",          # the credential — excluded
            "AGORA_CONTROL_PLANE_TOKEN": "tok",   # an Agora-local secret, AGORA_-prefixed
            "AWS_SECRET_ACCESS_KEY": "leak",      # arbitrary secret
            "PATH": "/usr/bin",
        },
    ))
    sent = captured["__env__"]
    # ONLY the explicit three agent-identity vars cross — a finite allowlist, not a prefix.
    assert sent == {"AGORA_TASK_ID": "t", "AGORA_RUN_ID": "r", "AGORA_PROJECT_ID": "p"}
    assert "AGORA_SSH_KEY_x" not in sent          # credential excluded
    assert "AGORA_CONTROL_PLANE_TOKEN" not in sent  # AGORA_-prefixed secret NOT leaked
    assert "AWS_SECRET_ACCESS_KEY" not in sent
    assert "PATH" not in sent


# --- RemoteProcessHandle basic contract ---

def test_remote_handle_pid_is_none_and_idempotent_stop(monkeypatch):
    h = RemoteProcessHandle(_FakeProc())
    assert h.pid is None
    _run(h.terminate()); _run(h.kill())  # no exception


# --- command construction: shlex safety + POSIX path normalization ---

def test_remote_cmd_shlex_quotes_every_arg_and_posix_cwd():
    from pathlib import PureWindowsPath
    # A prompt with shell metacharacters must not break out into extra remote words;
    # a Windows-client cwd must reach the Linux remote as a POSIX path.
    cmd = RemoteEnvironment._remote_cmd(
        ["agent", "run; rm -rf /", "a b"], PureWindowsPath("/home/ubuntu/wt")
    )
    # cwd normalized to forward slashes, quoted; injection payload is a single quoted arg
    assert cmd.startswith("cd /home/ubuntu/wt && ")
    assert "'run; rm -rf /'" in cmd            # the ; is inside quotes, not a separator
    assert "'a b'" in cmd                        # spaces quoted, not split
    # no unquoted metacharacter escaped into the command structure
    assert cmd.count("&&") == 1                  # only our own cd &&, not from the payload


def test_remote_cmd_no_cwd_is_just_the_quoted_argv():
    cmd = RemoteEnvironment._remote_cmd(["ls", "-la"], None)
    assert cmd == "ls -la"


def test_posix_normalizes_windows_path_but_passes_str_through():
    from agora.environments_remote import _posix
    from pathlib import PureWindowsPath, PurePosixPath
    assert _posix(PureWindowsPath("/a/b")) == "/a/b"      # backslashes -> forward
    assert _posix(PurePosixPath("/a/b")) == "/a/b"
    assert _posix("/already/posix") == "/already/posix"   # str passthrough unchanged


# --- run outcome: timeout, nonzero exit ---

def test_run_timeout_maps_to_timeout_outcome(monkeypatch):
    import asyncio as _a
    class Conn(_FakeConn):
        async def run(self, cmd, *, encoding=None, check=False):
            await _a.sleep(10)  # outlast the deadline
    env = _env(monkeypatch, Conn())
    r = _run(env.run(["sleep"], timeout=0.2))
    assert r.outcome == "timeout" and r.returncode is None and not r.ok


def test_run_nonzero_exit_is_completed_but_not_ok(monkeypatch):
    conn = _FakeConn(run_result=_FakeCompleted(3, b"", b"boom"))
    env = _env(monkeypatch, conn)
    r = _run(env.run(["false"], timeout=10))
    assert r.outcome == "completed" and r.returncode == 3
    assert r.ran and not r.ok and r.stderr == "boom"


def test_run_empty_argv_raises(monkeypatch):
    env = _env(monkeypatch, _FakeConn())
    with pytest.raises(ValueError):
        _run(env.run([], timeout=10))


# --- probe follow_symlinks=False sees the link itself ---

def test_probe_no_follow_reports_symlink_as_other(monkeypatch):
    from pathlib import Path
    entries = {"/link": _FakeAttrs(stat.S_IFLNK | 0o777)}
    env = _env(monkeypatch, _FakeConn(sftp=_FakeSFTP(entries=entries)))
    r = _run(env.probe(Path("/link"), follow_symlinks=False))
    assert r.kind == "other"  # the link itself, not its target


# --- retry: bounded + reconnect + final outcome ---

def test_read_only_retry_is_bounded_and_ends_unstartable(monkeypatch):
    from agora.environments import CONNECT_MAX_RETRIES
    calls = {"n": 0}
    class Conn(_FakeConn):
        async def run(self, cmd, *, encoding=None, check=False):
            calls["n"] += 1
            raise asyncssh.ConnectionLost("boom")
    env = _env(monkeypatch, Conn())
    r = _run(env.run(["git", "status"], timeout=10, read_only=True))
    assert calls["n"] == CONNECT_MAX_RETRIES + 1   # bounded: first + retries, no more
    assert r.outcome == "unstartable"               # gave up after the bound


# --- connect kwargs (CR-21) ---

def test_connect_never_reads_system_ssh_config(monkeypatch):
    """asyncssh.connect must be called with config=None, so it never reads the system
    ssh config (CR-21). Two reasons: every parameter is supplied explicitly, and on a
    Japanese Windows (cp932) asyncssh decoding ~/.ssh/config raises UnicodeDecodeError
    before the handshake. This drives the real _connect_once, not a _ensure_conn stub."""
    monkeypatch.setenv("R", "some-password")   # not a file path -> password credential
    captured = {}
    async def fake_connect(**kwargs):
        captured.update(kwargs)
        return _FakeConn()
    monkeypatch.setattr(asyncssh, "connect", fake_connect)
    env = RemoteEnvironment(host="h", user="u", secret_ref="R")
    _run(env._connect_once())
    assert "config" in captured and captured["config"] is None


# --- integration (real host) — skipped by default ---

@pytest.mark.integration
def test_real_remote_roundtrip():
    """Placeholder for the real connect+run against a live host. It always skips today —
    it does not yet read the environment or open a connection; the real round trip lands
    in the Build & Test stage.

    When implemented it will take its target from AGORA_SSH_TEST_HOST rather than a
    literal here: the host is on a private network and this repository is public. It will
    also need AGORA_SSH_KEY_<name> / AGORA_SSH_PASSWORD_<name> and a known_hosts entry
    for that host, since strict host-key checking is the default."""
    pytest.skip("integration: not implemented yet — real host round trip lands in Build & Test")
