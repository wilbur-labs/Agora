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
from agora.fsprobe import PathProbe


def _run(coro):
    return asyncio.run(coro)


class _FakeEnv:
    def __init__(self, git=None, probes=None):
        self._git = git or {}
        self._probes = probes or {}   # path str -> PathProbe for deletion-detection
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
        # Deletion detection probes a baseline path no longer in the status list.
        # Default: absent (the common "the run deleted it" case) unless configured.
        return self._probes.get(str(path), PathProbe(kind="absent"))


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
    snapshot = _run(d.snapshot_changes(Path(WS), baseline))
    # Same shape as local snapshot_changes: a dict with files + truncated.
    assert snapshot["truncated"] is False
    assert [c["path"] for c in snapshot["files"]] == ["new.py"]


def test_snapshot_none_baseline_returns_none():
    # None baseline = pre-run state unreadable -> cannot establish -> None (NOT []).
    # None and [] are different answers: "could not establish" vs "verified nothing".
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(" M x.py\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.snapshot_changes(Path(WS), None)) is None


def test_snapshot_incomplete_baseline_returns_none():
    # complete=False = pre-run state itself hit MAX_FILES, cannot subtract reliably
    # -> None (we cannot prove what this run owns; that differs from proving nothing).
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(" M x.py\x00"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.snapshot_changes(Path(WS), ({}, False))) is None


def test_snapshot_git_failure_returns_none():
    # A failed status is never frozen as "changed nothing" — it is None (unestablished).
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            CommandResult("unstartable", error=OSError("no git")),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.snapshot_changes(Path(WS), ({}, True))) is None


def test_snapshot_over_max_files_marks_truncated_and_skips_deletion_detection():
    from agora.execution.deliverables import MAX_FILES
    # MAX_FILES+1 changed entries: the list is capped and truncated=True. A baseline
    # path missing from the (capped) list must NOT be invented as a deletion when
    # truncated — it may simply be one that did not fit.
    payload = "".join(f" M f{i:04d}.py\x00" for i in range(MAX_FILES + 1))
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(payload),
    })
    d = RemoteDeliverables(env, _FakeFS())
    snapshot = _run(d.snapshot_changes(Path(WS), ({"gone.py": "modified"}, True)))
    assert snapshot["truncated"] is True
    assert len(snapshot["files"]) == MAX_FILES
    assert not any(c["path"] == "gone.py" for c in snapshot["files"])  # no invented deletion


def test_snapshot_deletion_detection_is_three_state():
    # A baseline path absent from the complete list is classified by a remote probe,
    # not assumed deleted — matching local snapshot_changes' lstat three-state:
    #   absent  -> "deleted"
    #   present -> "no longer differs" (run reverted/committed it; file still there)
    #   unknown -> "unreadable"
    # Inventing "deleted" for a still-present file would tell review/restore a file
    # that is sitting right there should be gone (the bug CR-14 round 2 caught).
    env = _FakeEnv(
        git={("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(" M kept.py\x00")},
        probes={
            f"{WS}/removed.py": PathProbe(kind="absent"),
            f"{WS}/reverted.py": PathProbe(kind="file"),
            f"{WS}/locked.py": PathProbe(kind="unknown", error=PermissionError("denied")),
        },
    )
    d = RemoteDeliverables(env, _FakeFS())
    baseline = ({
        "kept.py": "modified", "removed.py": "modified",
        "reverted.py": "modified", "locked.py": "modified",
    }, True)
    snapshot = _run(d.snapshot_changes(Path(WS), baseline))
    by_path = {c["path"]: c["status"] for c in snapshot["files"]}
    assert by_path["removed.py"] == "deleted"
    assert by_path["reverted.py"] == "no longer differs"
    assert by_path["locked.py"] == "unreadable"
    # kept.py was unchanged since baseline -> not delivered
    assert "kept.py" not in by_path


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


def test_handoff_aborts_when_status_truncated():
    # > MAX_FILES changes: `git add -A` would commit everything but the returned file
    # list is capped -> incomplete manifest. Fail closed BEFORE any mutation rather
    # than bind a handoff that undercounts what it committed (CR-14 round 2, High#2).
    from agora.execution.deliverables import MAX_FILES
    payload = "".join(f" M f{i:04d}.py\x00" for i in range(MAX_FILES + 1))
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(payload),
    })
    d = RemoteDeliverables(env, _FakeFS())
    with pytest.raises(OSError, match="incomplete"):
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



# --- W3-a U5 review round (kiro): identity, HEAD, renames, dirty-then-committed ---------

def _sha(path_and_digest: dict) -> CommandResult:
    lines = "".join(f"{digest}  {path}\n" for path, digest in path_and_digest.items())
    return CommandResult("completed", returncode=0, stdout=lines, stderr="")


def test_baseline_without_a_readable_head_is_no_baseline():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(""),
        ("rev-parse", "HEAD"): CommandResult("completed", returncode=128, stdout="", stderr="fatal"),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d.baseline_state(Path(WS))) is None


def test_a_dirty_file_the_run_committed_is_recorded_as_committed_not_as_no_longer_differs():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(""),  # clean now
        ("rev-parse", "HEAD"): CommandResult("completed", returncode=0, stdout="h2\n", stderr=""),
        ("diff", "--name-status", "-z", "h1..h2"): CommandResult("completed", returncode=0, stdout="M\x00a.py\x00", stderr=""),
        ("sha256sum", "--", f"{WS}/a.py"): _sha({f"{WS}/a.py": "b" * 64}),
    })
    d = RemoteDeliverables(env, _FakeFS())
    baseline = ({"a.py": {"status": "modified", "sha256": "a" * 64}}, True, "h1")
    snapshot = _run(d.snapshot_changes(Path(WS), baseline))
    assert [(f["path"], f["status"], f.get("committed_during_run"), f["sha256"]) for f in snapshot["files"]] == [
        ("a.py", "modified", True, "b" * 64),
    ]


def test_a_committed_rename_records_the_old_path_as_deleted():
    env = _FakeEnv(git={
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"): _status(""),
        ("rev-parse", "HEAD"): CommandResult("completed", returncode=0, stdout="h2\n", stderr=""),
        ("diff", "--name-status", "-z", "h1..h2"): CommandResult("completed", returncode=0, stdout="R100\x00old.json\x00new.json\x00", stderr=""),
        ("sha256sum", "--", f"{WS}/new.json"): _sha({f"{WS}/new.json": "c" * 64}),
    })
    d = RemoteDeliverables(env, _FakeFS())
    snapshot = _run(d.snapshot_changes(Path(WS), ({}, True, "h1")))
    assert [(f["path"], f["status"]) for f in snapshot["files"]] == [("new.json", "renamed"), ("old.json", "deleted")]


def test_digests_fail_closed_when_a_hashed_line_does_not_map_back():
    env = _FakeEnv(git={
        ("sha256sum", "--", f"{WS}/a.py", f"{WS}/b.py"): CommandResult(
            "completed", returncode=0, stdout=f"{'d' * 64}  {WS}/a.py\ngarbage line\n", stderr="",
        ),
    })
    d = RemoteDeliverables(env, _FakeFS())
    assert _run(d._digests(Path(WS), ["a.py", "b.py"])) is None
    # A GNU-escaped name is unescaped back to git's spelling.
    env2 = _FakeEnv(git={
        ("sha256sum", "--", f"{WS}/we\\ird.py"): CommandResult(
            "completed", returncode=0, stdout=f"\\{'e' * 64}  {WS}/we\\\\ird.py\n", stderr="",
        ),
    })
    d2 = RemoteDeliverables(env2, _FakeFS())
    assert _run(d2._digests(Path(WS), ["we\\ird.py"])) == {"we\\ird.py": "e" * 64}
