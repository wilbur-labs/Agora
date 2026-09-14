"""RemoteDeliverables (U4) over fake async environment + filesystem.

Explicit hand-written fakes (matching the U2/U3 suites — no unittest.mock), driven
with asyncio.run. Verify the SHAPE of U4's behaviours: change-list parsing over the
shared porcelain-z parser, snapshot baseline subtraction, read_file remote confinement
+ bounds + redaction, handoff commit list captured before commit, and the contract's
error modes (git failure -> [], unreadable file -> raise).
"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

import pytest

from agora.environments import CommandResult
from agora.execution.deliverables_remote import RemoteDeliverables


def _run(coro):
    return asyncio.run(coro)


class _FakeEnv:
    def __init__(self, git=None):
        self._git = git or {}
        self.calls: list[tuple[str, ...]] = []
        self.readonly: list[bool] = []  # read_only flag per call, positionally aligned

    async def run(self, argv, *, cwd=None, timeout, read_only=False):
        self.calls.append(tuple(argv))
        self.readonly.append(read_only)
        key = tuple(a for a in argv if a not in ("git",))
        for k in (key, tuple(argv[3:])):
            if k in self._git:
                return self._git[k]
        return CommandResult("completed", returncode=0, stdout="", stderr="")

    async def probe(self, path, *, follow_symlinks=True):
        raise NotImplementedError


class _FakeFS:
    def __init__(self, realpaths=None, files=None, raise_on=None):
        self._realpaths = realpaths or {}
        self._files = files or {}
        self._raise_on = raise_on or {}

    async def realpath(self, path):
        s = str(path)
        return PurePosixPath(self._realpaths.get(s, s))

    async def read_file(self, path, *, max_bytes=1 << 20, truncate=False):
        s = str(path)
        if s in self._raise_on:
            raise self._raise_on[s]
        if s not in self._files:
            raise FileNotFoundError(s)
        data = self._files[s]
        if not truncate and len(data) > max_bytes:
            raise OSError(f"{s} exceeds cap {max_bytes}")
        return data[: max_bytes]

    async def is_empty_dir(self, path):
        raise NotImplementedError

    async def write_file(self, path, content):
        raise NotImplementedError

    async def mkdir(self, path):
        raise NotImplementedError

    async def rmdir(self, path):
        raise NotImplementedError


WS = "/home/u/proj/.agora/workspaces/codex"


def _status(stdout):
    return CommandResult("completed", returncode=0, stdout=stdout, stderr="")


# --- list_changes ---

def test_list_changes_parses_porcelain_z():
    # Two changed files via the shared porcelain-z parser (NUL-separated).
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            _status(" M src/a.py\x00?? new.txt\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    changes = _run(d.list_changes(Path(WS)))
    paths = [c["path"] for c in changes]
    assert paths == ["new.txt", "src/a.py"]  # sorted
    assert {c["status"] for c in changes} == {"modified", "untracked"}


def test_list_changes_git_failure_returns_empty():
    # A git failure yields [] (indistinguishable from clean), never an exception.
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            CommandResult("unstartable", error=OSError("no git")),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.list_changes(Path(WS))) == []


def test_list_changes_caps_at_max_files():
    from agora.execution.deliverables import MAX_FILES
    stdout = "".join(f" M f{i}.py\x00" for i in range(MAX_FILES + 50))
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(stdout),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert len(_run(d.list_changes(Path(WS)))) == MAX_FILES


# --- snapshot_changes ---

def test_snapshot_subtracts_baseline():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            _status(" M kept.py\x00 M new.py\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    # kept.py was already "modified" before the run; new.py is this run's.
    baseline = ({"kept.py": "modified"}, True)
    delivered = _run(d.snapshot_changes(Path(WS), baseline))
    assert [c["path"] for c in delivered] == ["new.py"]


def test_snapshot_none_baseline_returns_empty():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(" M x.py\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.snapshot_changes(Path(WS), None)) == []


def test_snapshot_incomplete_baseline_returns_empty():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(" M x.py\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.snapshot_changes(Path(WS), ({}, False))) == []


# --- read_file (remote confinement + bounds + redaction) ---

def test_read_file_confined_bounded_redacted():
    from agora.execution.deliverables import MAX_FILE_BYTES
    target = f"{WS}/out.txt"
    content = (b"api_key=SECRETVALUE12345\n" + b"x" * (MAX_FILE_BYTES + 10))
    fs = _FakeFS(
        realpaths={WS: WS, target: target},
        files={target: content},
    )
    d = RemoteDeliverables(_FakeEnv(), fs)
    result = _run(d.read_file(Path(WS), "out.txt"))
    assert result["truncated"] is True
    assert "[REDACTED]" in result["content"]
    assert "SECRETVALUE12345" not in result["content"]


def test_read_file_rejects_escaping_path():
    # The path resolves (on the remote) OUTSIDE the workspace -> PermissionError.
    fs = _FakeFS(realpaths={WS: WS, f"{WS}/../../etc/passwd": "/etc/passwd"})
    d = RemoteDeliverables(_FakeEnv(), fs)
    with pytest.raises(PermissionError, match="escapes"):
        _run(d.read_file(Path(WS), "../../etc/passwd"))


def test_read_file_missing_raises_not_empty():
    fs = _FakeFS(realpaths={WS: WS, f"{WS}/gone.txt": f"{WS}/gone.txt"})
    d = RemoteDeliverables(_FakeEnv(), fs)
    with pytest.raises(FileNotFoundError):
        _run(d.read_file(Path(WS), "gone.txt"))


# --- handoff_commit ---

def test_handoff_captures_files_before_commit():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            _status(" M a.py\x00?? b.txt\x00"),
        ("rev-parse", "HEAD"): _status("abc123\n"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    result = _run(d.handoff_commit(Path(WS), "handoff"))
    assert result["commit"] == "abc123"
    assert sorted(result["files"]) == ["a.py", "b.txt"]
    # Read-only safety: the status query is read_only=True (retryable); add/commit are
    # mutating and MUST be read_only=False (a retry could double the commit). Locking
    # this so flipping add/commit to read_only=True fails the test.
    def _subcmd(argv):
        # first token that is not `git`, `-C <cwd>`, or a `-c key=val` config flag
        i = 0
        toks = list(argv)
        while i < len(toks):
            t = toks[i]
            if t == "git":
                i += 1
            elif t in ("-C", "-c"):
                i += 2
            else:
                return t
        return ""
    ro_by_sub = {}
    order = []
    for argv, ro in zip(env.calls, env.readonly):
        sub = _subcmd(argv)
        ro_by_sub[sub] = ro
        order.append(sub)
    assert ro_by_sub["status"] is True
    assert ro_by_sub["add"] is False
    assert ro_by_sub["commit"] is False
    assert order.index("status") < order.index("add") < order.index("commit")


def test_handoff_aborts_when_status_unreadable():
    # status query fails -> handoff must ABORT before any mutation, not commit files=[].
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            CommandResult("unstartable", error=OSError("no git")),
    })
    d = RemoteDeliverables(env, _FakeFS())
    with pytest.raises(OSError, match="refusing to commit"):
        _run(d.handoff_commit(Path(WS), "m"))
    assert not any("commit" in c for c in env.calls)
    assert not any("add" in c for c in env.calls)


def test_read_file_large_returns_truncated_preview_not_raise():
    # A file larger than MAX_FILE_BYTES must return a TRUNCATED preview, not raise
    # (High#1: the preview read is truncate=True, U3 returns bounded bytes).
    from agora.execution.deliverables import MAX_FILE_BYTES
    target = f"{WS}/big.log"
    big = b"L" * (MAX_FILE_BYTES * 3)
    fs = _FakeFS(realpaths={WS: WS, target: target}, files={target: big})
    d = RemoteDeliverables(_FakeEnv(), fs)
    result = _run(d.read_file(Path(WS), "big.log"))
    assert result["truncated"] is True
    assert len(result["content"]) <= MAX_FILE_BYTES


def test_handoff_git_failure_raises():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(""),
        ("add", "-A"): CommandResult("completed", returncode=1, stdout="", stderr="add failed"),
    })
    # need to key "add -A" through the full-args path
    d = RemoteDeliverables(env, _FakeFS())
    with pytest.raises(OSError):
        _run(d.handoff_commit(Path(WS), "m"))
