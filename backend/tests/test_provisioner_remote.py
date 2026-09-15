"""RemoteWorkspaceProvisioner (U3) over fake async environment + filesystem.

No real SSH host: explicit hand-written fakes (matching test_environments_remote's
style — no unittest.mock) drive the provisioner and let each test assert exactly which
git commands and filesystem operations crossed the seam. Real transport is left to the
integration test (Build & Test, real host).

These verify the SHAPE of the U3 behaviours that matter for FR-5 / D2 form-2:
root-must-pre-exist, remote confinement via realpath, complete-proof emptiness, the
fail-closed rules, and teardown ownership verification.

Driven with asyncio.run, like the rest of the async suites.
"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

import pytest

from agora.environments import CommandResult
from agora.fsprobe import PathProbe
from agora.workspaces.models import ProvisionRequest, WorkspaceState
from agora.workspaces.provisioner import (
    WorkspaceConflictError,
    WorkspaceUnavailableError,
    WorkspaceValidationError,
)
from agora.workspaces.provisioner_remote import RemoteWorkspaceProvisioner


def _run(coro):
    return asyncio.run(coro)


# --- fakes ---

class _FakeProject:
    def __init__(self, project_id, root, workspaces):
        self.project_id = project_id
        self.root = root
        self.workspaces = workspaces


class _FakeRegistry:
    def __init__(self, project):
        self._project = project

    def get(self, project_id):
        return self._project


class _FakeEnv:
    """Async run/probe. `git` maps an argv-tuple (after the git command) to a
    CommandResult; `probes` maps a str path to a PathProbe. Records every git argv."""
    def __init__(self, git=None, probes=None):
        self._git = git or {}
        self._probes = probes or {}
        self.calls: list[tuple[str, ...]] = []

    async def run(self, argv, *, cwd=None, timeout, read_only=False):
        self.calls.append(tuple(argv))
        key = tuple(argv[1:])  # drop the "git" command
        if key in self._git:
            return self._git[key]
        # default: a clean success with empty output
        return CommandResult("completed", returncode=0, stdout="", stderr="")

    async def probe(self, path, *, follow_symlinks=True):
        return self._probes.get(str(path), PathProbe("absent"))


class _FakeFS:
    """Async filesystem. `realpaths` maps input->output (identity if absent);
    `empty` is the set of paths is_empty_dir returns True for; records mutations."""
    def __init__(self, realpaths=None, empty=(), raise_on=None):
        self._realpaths = realpaths or {}
        self._empty = set(empty)
        self._raise_on = raise_on or {}
        self.writes: list[str] = []
        self.mkdirs: list[str] = []
        self.rmdirs: list[str] = []

    async def realpath(self, path):
        s = str(path)
        if s in self._raise_on.get("realpath", ()):
            raise OSError(f"cannot resolve {s}")
        return PurePosixPath(self._realpaths.get(s, s))

    async def is_empty_dir(self, path):
        s = str(path)
        if s in self._raise_on.get("is_empty_dir", ()):
            raise OSError(f"cannot list {s}")
        return s in self._empty

    async def read_file(self, path, *, max_bytes=1 << 20):
        raise NotImplementedError

    async def write_file(self, path, content):
        self.writes.append(str(path))

    async def mkdir(self, path):
        self.mkdirs.append(str(path))

    async def rmdir(self, path):
        self.rmdirs.append(str(path))


def _ok(stdout=""):
    return CommandResult("completed", returncode=0, stdout=stdout, stderr="")


def _project():
    return _FakeProject(
        "proj",
        PurePosixPath("/home/u/proj"),
        {"codex": PurePosixPath("/home/u/proj/.agora/workspaces/codex")},
    )


def _provisioner(env, fs, project=None):
    project = project or _project()
    return RemoteWorkspaceProvisioner(
        _FakeRegistry(project),
        env,
        fs,
        remote_project_root=PurePosixPath("/home/u/proj"),
    )


# --- D2 form-2: never create the root ---

def test_ensure_source_repo_missing_root_raises_not_creates():
    # Probe of the root comes back absent -> must raise, and NEVER mkdir the root.
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("absent")})
    fs = _FakeFS()
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="does not exist"):
        _run(p.ensure_source_repo("proj"))
    assert fs.mkdirs == []  # the root was never created


def test_ensure_source_repo_unknown_root_fails_closed_unavailable():
    # An unreadable root is NOT "absent" (create it) — it is unavailable (fix it).
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("unknown", error=PermissionError())})
    p = _provisioner(env, _FakeFS())
    with pytest.raises(WorkspaceUnavailableError):
        _run(p.ensure_source_repo("proj"))


def test_ensure_source_repo_seeds_only_on_definite_gitignore_absence():
    # Root exists, is its own repo, no HEAD, empty index, .gitignore ABSENT -> write it.
    env = _FakeEnv(
        git={
            ("rev-parse", "--show-toplevel"): _ok("/home/u/proj"),
            ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--verify", "--quiet", "HEAD"): CommandResult("completed", returncode=1, stdout="", stderr=""),
            ("ls-files",): _ok(""),
        },
        probes={
            "/home/u/proj": PathProbe("dir"),
            "/home/u/proj/.gitignore": PathProbe("absent"),
        },
    )
    fs = _FakeFS()
    p = _provisioner(env, fs)
    assert _run(p.ensure_source_repo("proj")) is True
    assert fs.writes == ["/home/u/proj/.gitignore"]


def test_ensure_source_repo_unknown_gitignore_not_overwritten():
    # An unreadable .gitignore must NOT be overwritten (fsprobe rule).
    env = _FakeEnv(
        git={
            ("rev-parse", "--show-toplevel"): _ok("/home/u/proj"),
            ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--verify", "--quiet", "HEAD"): CommandResult("completed", returncode=1, stdout="", stderr=""),
            ("ls-files",): _ok(""),
        },
        probes={
            "/home/u/proj": PathProbe("dir"),
            "/home/u/proj/.gitignore": PathProbe("unknown", error=PermissionError()),
        },
    )
    fs = _FakeFS()
    _run(_provisioner(env, fs).ensure_source_repo("proj"))
    assert fs.writes == []  # unreadable -> not written over


def test_ensure_source_repo_staged_index_fails_closed():
    env = _FakeEnv(
        git={
            ("rev-parse", "--show-toplevel"): _ok("/home/u/proj"),
            ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--verify", "--quiet", "HEAD"): CommandResult("completed", returncode=1, stdout="", stderr=""),
            ("ls-files",): _ok("already/staged.py"),
        },
        probes={"/home/u/proj": PathProbe("dir")},
    )
    with pytest.raises(WorkspaceValidationError, match="staged"):
        _run(_provisioner(env, _FakeFS()).ensure_source_repo("proj"))


# --- confinement (remote realpath) ---

def test_provision_rejects_workspace_outside_allowed_root():
    # realpath reveals the workspace resolves OUTSIDE the project root -> reject.
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("dir")})
    fs = _FakeFS(realpaths={ws: "/etc/evil", "/home/u/proj": "/home/u/proj"})
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="outside"):
        _run(p.provision(ProvisionRequest(project_id="proj", adapter="codex")))


def test_provision_rejects_workspace_equal_to_root():
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("dir")})
    # realpath collapses the workspace onto the root itself.
    fs = _FakeFS(realpaths={ws: "/home/u/proj", "/home/u/proj": "/home/u/proj"})
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="replace the project root"):
        _run(p.provision(ProvisionRequest(project_id="proj", adapter="codex")))


def test_confinement_component_boundary_not_string_prefix():
    # /home/u/proj-evil is NOT within /home/u/proj even though the string is a prefix.
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("dir")})
    fs = _FakeFS(realpaths={ws: "/home/u/proj-evil/ws", "/home/u/proj": "/home/u/proj"})
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="outside"):
        _run(p.provision(ProvisionRequest(project_id="proj", adapter="codex")))


# --- empty-dir gating on provision ---

def test_provision_present_nonempty_workspace_conflicts():
    # A present, non-empty dir that is not our worktree is FOREIGN — _inspect catches
    # it before the empty-dir gate, and it is never removed.
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(
        git={
            ("rev-parse", "--show-toplevel"): _ok("/home/u/proj"),
            ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
            ("--version",): _ok("git version 2.34.1"),
        },
        probes={"/home/u/proj": PathProbe("dir"), ws: PathProbe("dir")},
    )
    fs = _FakeFS(realpaths={ws: ws, "/home/u/proj": "/home/u/proj"}, empty=())  # not empty
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceConflictError):
        _run(p.provision(ProvisionRequest(project_id="proj", adapter="codex")))
    assert fs.rmdirs == []  # a non-empty dir is never removed


def test_provision_is_empty_dir_failure_does_not_read_as_empty():
    # is_empty_dir raising (permission/transport) must propagate, never be read as empty
    # (which would let us rmdir + provision over an unknowable directory).
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(
        git={
            ("rev-parse", "--show-toplevel"): _ok("/home/u/proj"),
            ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
            ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
            ("--version",): _ok("git version 2.34.1"),
        },
        probes={"/home/u/proj": PathProbe("dir"), ws: PathProbe("dir")},
    )
    fs = _FakeFS(realpaths={ws: ws, "/home/u/proj": "/home/u/proj"}, raise_on={"is_empty_dir": (ws,)})
    p = _provisioner(env, fs)
    with pytest.raises(OSError):
        _run(p.provision(ProvisionRequest(project_id="proj", adapter="codex")))
    assert fs.rmdirs == []


# --- teardown ownership ---

def test_teardown_refuses_when_not_a_verified_worktree():
    # is_ready_worktree returns False (source repo query fails) -> no removal.
    env = _FakeEnv(git={
        ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            CommandResult("completed", returncode=1, stdout="", stderr="not a repo"),
    })
    p = _provisioner(env, _FakeFS())
    _run(p.teardown_worktree("proj", "codex"))
    assert not any(c[:2] == ("worktree", "remove") for c in env.calls)


def test_teardown_unknown_adapter_is_noop():
    env = _FakeEnv()
    p = _provisioner(env, _FakeFS())
    _run(p.teardown_worktree("proj", "nonexistent"))
    assert env.calls == []


# --- codex round-1 (Step 2) regression locks ---

def test_configured_root_mismatch_rejected_before_any_write():
    # remote_project_root and project.root resolve to DIFFERENT remote dirs -> refuse,
    # before any git init / mkdir. Guards against operating outside the configured root.
    env = _FakeEnv(probes={"/home/u/proj": PathProbe("dir")})
    fs = _FakeFS(realpaths={"/home/u/proj": "/home/u/proj", "/configured/elsewhere": "/configured/elsewhere"})
    project = _project()
    p = RemoteWorkspaceProvisioner(
        _FakeRegistry(project), env, fs, remote_project_root=PurePosixPath("/configured/elsewhere")
    )
    with pytest.raises(WorkspaceValidationError, match="does not"):
        _run(p.ensure_source_repo("proj"))
    assert env.calls == []  # no git ran


def test_indeterminate_repo_probe_does_not_trigger_init():
    # The init decision is gated on a TYPED .git probe, not git's ambiguous exit codes.
    # An UNREADABLE .git (unknown) must raise, never init — we cannot tell if a repo is
    # there. (A definitely-absent .git is the only thing that inits.)
    env = _FakeEnv(
        probes={
            "/home/u/proj": PathProbe("dir"),
            "/home/u/proj/.git": PathProbe("unknown", error=PermissionError()),
        },
    )
    p = _provisioner(env, _FakeFS())
    with pytest.raises(WorkspaceUnavailableError):
        _run(p.ensure_source_repo("proj"))
    assert ("init",) not in [c[1:] for c in env.calls]


def test_dubious_ownership_does_not_trigger_init():
    # .git PRESENT but the root is not its own repo (e.g. dubious ownership / nested) ->
    # refuse to init over it, never read as "no repo".
    env = _FakeEnv(
        git={("rev-parse", "--show-toplevel"): CommandResult("completed", returncode=128, stdout="", stderr="dubious ownership")},
        probes={
            "/home/u/proj": PathProbe("dir"),
            "/home/u/proj/.git": PathProbe("dir"),  # .git present
        },
    )
    p = _provisioner(env, _FakeFS(realpaths={"/home/u/proj": "/home/u/proj"}))
    with pytest.raises(WorkspaceValidationError, match="not its own repository"):
        _run(p.ensure_source_repo("proj"))
    assert ("init",) not in [c[1:] for c in env.calls]


def test_indeterminate_branch_check_does_not_unlock_worktree_add():
    # show-ref (branch existence) TIMES OUT -> _branch_exists must raise, never read as
    # "branch absent" (which would run `worktree add -b`, creating a branch wrongly).
    env = _FakeEnv(git={
        ("show-ref", "--verify", "--quiet", "refs/heads/x"): CommandResult("timeout"),
    })
    p = _provisioner(env, _FakeFS())
    with pytest.raises(WorkspaceUnavailableError):
        _run(p._branch_exists(PurePosixPath("/home/u/proj"), "x"))


def test_ready_requires_worktree_registration_not_just_matching_metadata():
    # A subdir of the source repo shares common-dir/branch/HEAD but is NOT a registered
    # linked worktree and is not its own top-level -> must NOT be READY.
    ws = "/home/u/proj/subdir"
    root = "/home/u/proj"
    env = _FakeEnv(git={
        # source root is a real own-repo
        ("rev-parse", "--show-toplevel"): _ok(root),
        ("rev-parse", "--absolute-git-dir"): _ok("/home/u/proj/.git"),
        ("rev-parse", "--path-format=absolute", "--git-common-dir"): _ok("/home/u/proj/.git"),
        # the worktree list does NOT register /home/u/proj/subdir
        ("worktree", "list", "--porcelain"): _ok("worktree /home/u/proj\nHEAD abc\n"),
    })
    fs = _FakeFS(realpaths={ws: ws, root: root, "/home/u/proj/.git": "/home/u/proj/.git"})
    p = _provisioner(env, fs)
    # ws --show-toplevel returns the ROOT (subdir is not its own worktree root)
    # -> is_ready_worktree returns False at the top-level check.
    ready = _run(p.is_ready_worktree(PurePosixPath(ws), PurePosixPath(root), "agora/workspace/x"))
    assert ready is False


def test_teardown_enforces_confinement_before_removal():
    # A workspace that resolves OUTSIDE the allowed root must be refused by teardown's
    # confinement check even before ownership — git registration alone is not Agora's
    # allowed-directory boundary.
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv()
    fs = _FakeFS(realpaths={ws: "/etc/evil", "/home/u/proj": "/home/u/proj"})
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="outside"):
        _run(p.teardown_worktree("proj", "codex"))
    assert not any(c[:2] == ("worktree", "remove") for c in env.calls)


# --- codex round-2 (Step 2) regression locks ---

def test_branch_check_nonzero_non_absent_exit_is_indeterminate():
    # show-ref exiting 127 (shell error, git not found) is NOT "branch absent" — it must
    # raise, never unlock `worktree add -b`. Only exit 1 (the ref-missing code) is absent.
    env = _FakeEnv(git={
        ("show-ref", "--verify", "--quiet", "refs/heads/x"):
            CommandResult("completed", returncode=127, stdout="", stderr="git: not found"),
    })
    p = _provisioner(env, _FakeFS())
    with pytest.raises(WorkspaceUnavailableError):
        _run(p._branch_exists(PurePosixPath("/home/u/proj"), "x"))


def test_is_ready_rejects_main_worktree():
    # workspace == project_root is the MAIN worktree, never a linked one — reject even if
    # branch/HEAD would match.
    root = "/home/u/proj"
    env = _FakeEnv()
    fs = _FakeFS(realpaths={root: root})
    p = _provisioner(env, fs)
    ready = _run(p.is_ready_worktree(PurePosixPath(root), PurePosixPath(root), "agora/workspace/x"))
    assert ready is False


def test_ensure_parent_dir_creates_missing_levels_but_never_root():
    # Default workspace <root>/.agora/workspaces/codex: .agora and .agora/workspaces are
    # absent; both get created (in order), the root NEVER does.
    root = "/home/u/proj"
    ws = "/home/u/proj/.agora/workspaces/codex"
    env = _FakeEnv(probes={
        root: PathProbe("dir"),
        "/home/u/proj/.agora": PathProbe("absent"),
        "/home/u/proj/.agora/workspaces": PathProbe("absent"),
    })
    fs = _FakeFS(realpaths={
        root: root, "/home/u/proj/.agora/workspaces": "/home/u/proj/.agora/workspaces",
    })
    p = _provisioner(env, fs)
    _run(p._ensure_parent_dir(PurePosixPath(ws), PurePosixPath(root)))
    assert fs.mkdirs == ["/home/u/proj/.agora", "/home/u/proj/.agora/workspaces"]
    assert root not in fs.mkdirs  # the root is never created


def test_ensure_parent_dir_fails_closed_if_root_vanished():
    root = "/home/u/proj"
    ws = "/home/u/proj/.agora/workspaces/codex"
    # parent resolves to root (direct child scenario via realpath), root now absent
    env = _FakeEnv(probes={root: PathProbe("absent")})
    fs = _FakeFS(realpaths={
        root: root, "/home/u/proj/.agora/workspaces": root,  # parent collapses onto root
    })
    p = _provisioner(env, fs)
    with pytest.raises(WorkspaceValidationError, match="disappeared"):
        _run(p._ensure_parent_dir(PurePosixPath(ws), PurePosixPath(root)))
    assert fs.mkdirs == []


def test_ensure_parent_dir_accepts_allowed_external_root():
    # A workspace under an explicitly-allowed external root (not under the project root)
    # must be buildable — _assert_allowed permits it, so _ensure_parent_dir must too.
    root = "/home/u/proj"
    ext = "/srv/workspaces"
    ws = "/srv/workspaces/codex"
    env = _FakeEnv(probes={ext: PathProbe("dir"), "/srv/workspaces/codex": PathProbe("absent")})
    fs = _FakeFS(realpaths={root: root, ext: ext, ws: ws})
    project = _project()
    p = RemoteWorkspaceProvisioner(
        _FakeRegistry(project), env, fs,
        remote_project_root=PurePosixPath(root),
        allowed_workspace_roots=[PurePosixPath(ext)],
    )
    # parent of the workspace is /srv/workspaces (the allowed root, already present) ->
    # nothing to create, and NOT rejected as "outside".
    _run(p._ensure_parent_dir(PurePosixPath(ws), PurePosixPath(root)))
    assert fs.mkdirs == []  # parent already exists


def test_ensure_parent_dir_creates_missing_allowed_base_and_levels():
    # Allowed root /srv/workspaces is ABSENT (its parent /srv exists); workspace is
    # /srv/workspaces/team/codex. The allowed base itself and the intermediate level are
    # created, in order, top-down. The base is Agora-managed, so creating it is allowed.
    root = "/home/u/proj"
    ext = "/srv/workspaces"
    ws = "/srv/workspaces/team/codex"
    env = _FakeEnv(probes={
        ext: PathProbe("absent"),
        "/srv/workspaces/team": PathProbe("absent"),
    })
    fs = _FakeFS(realpaths={root: root, ext: ext, "/srv/workspaces/team": "/srv/workspaces/team"})
    project = _project()
    p = RemoteWorkspaceProvisioner(
        _FakeRegistry(project), env, fs,
        remote_project_root=PurePosixPath(root),
        allowed_workspace_roots=[PurePosixPath(ext)],
    )
    _run(p._ensure_parent_dir(PurePosixPath(ws), PurePosixPath(root)))
    assert fs.mkdirs == ["/srv/workspaces", "/srv/workspaces/team"]

