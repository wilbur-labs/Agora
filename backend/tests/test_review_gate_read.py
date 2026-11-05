"""The overview's check of the commit review gate's record (workspace CR-75 slice 2, part D).
The gate's hooks are claude-workspace's; here commits carry the trailers they would write."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agora.projects import review_gate
from agora.projects.review_gate import DIFF_OPTS, read_review_gate

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True,
                          check=True).stdout.decode("utf-8")


@pytest.fixture(autouse=True)
def _fresh_cache():
    review_gate._patch_ids.clear()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "t")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "core.autocrlf", "false")
    (r / "base.txt").write_text("base\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "before the gate")
    return r


def install(repo: Path, commit: str | None = "HEAD") -> None:
    state = repo / ".git" / "agora-review"
    state.mkdir(parents=True, exist_ok=True)
    sha = git(repo, "rev-parse", commit).strip() if commit else None
    (state / "installed").write_text(json.dumps({"at": "2026-11-05T00:00:00Z", "commit": sha}))


def staged_patch(repo: Path) -> str:
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True, check=False).stdout.decode().strip()
    base = head or EMPTY_TREE
    diff = subprocess.run(["git", "-C", str(repo), "diff", *DIFF_OPTS, "--cached", base],
                          capture_output=True, check=True).stdout
    out = git(repo, "patch-id", "--stable", stdin=diff).split()
    return out[0] if out else "none"


def commit(repo: Path, name: str, *trailers: str, patch: str | None = "auto") -> None:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    git(repo, "add", name)
    lines = list(trailers)
    if patch == "auto":
        lines.append(f"Review-patch: {staged_patch(repo)}")
    elif patch:
        lines.append(f"Review-patch: {patch}")
    message = f"add {name}\n\n" + "\n".join(lines) + ("\n" if lines else "")
    git(repo, "commit", "-q", "-F", "-", stdin=message.encode("utf-8"))


def counts(read) -> dict:
    p = read.payload()
    return {k: p[k] for k in ("reviewed", "skipped", "merges", "unrecorded")}


def test_without_the_gate_nothing_is_said(repo):
    assert read_review_gate(str(repo)).payload() == {"state": "not_installed", "reason": None}
    assert read_review_gate(None).state == "not_installed"
    assert read_review_gate(str(repo / "nope")).state == "not_installed"


def test_commits_are_sorted_by_what_their_trailers_prove(repo):
    install(repo)
    commit(repo, "a.txt", "Reviewed-by: codex")
    commit(repo, "b.txt", "Reviewed-by: codex, kiro")
    commit(repo, "c.txt", "Review-skipped: release version bump")
    commit(repo, "d.txt", patch=None)                       # --no-verify: no trailer
    commit(repo, "e.txt", "Reviewed-by: Alice")             # a person, not the gate
    commit(repo, "f.txt", "Reviewed-by: codex", patch="0" * 40)  # carried along, wrong patch
    commit(repo, "g.txt", "Reviewed-by: codex", patch=None)      # reviewer but no patch
    read = read_review_gate(str(repo))
    assert read.state == "read" and not read.truncated
    assert counts(read) == {"reviewed": 2, "skipped": 1, "merges": 0, "unrecorded": 4}


def test_commits_before_the_install_are_not_counted(repo):
    commit(repo, "old.txt", patch=None)
    install(repo)
    commit(repo, "new.txt", "Reviewed-by: kiro")
    assert counts(read_review_gate(str(repo))) == {"reviewed": 1, "skipped": 0, "merges": 0, "unrecorded": 0}


def test_an_install_on_an_empty_repository_counts_everything(repo):
    install(repo, commit=None)
    assert counts(read_review_gate(str(repo)))["unrecorded"] == 1  # "before the gate"


def test_a_merge_is_counted_as_a_merge(repo):
    install(repo)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side.txt", "Reviewed-by: codex")
    git(repo, "checkout", "-q", "main")
    commit(repo, "main.txt", "Reviewed-by: codex")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    assert counts(read_review_gate(str(repo))) == {"reviewed": 2, "skipped": 0, "merges": 1, "unrecorded": 0}


def test_a_clean_rebase_keeps_a_reviewed_commit_reviewed(repo):
    install(repo)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side.txt", "Reviewed-by: codex")
    git(repo, "checkout", "-q", "main")
    commit(repo, "main.txt", "Reviewed-by: codex")
    git(repo, "checkout", "-q", "side")
    git(repo, "rebase", "-q", "main")
    assert counts(read_review_gate(str(repo)))["reviewed"] == 2


def test_more_than_the_limit_is_said(repo, monkeypatch):
    monkeypatch.setattr(review_gate, "LIMIT", 2)
    install(repo)
    for name in ("a.txt", "b.txt", "c.txt"):
        commit(repo, name, "Reviewed-by: codex")
    read = read_review_gate(str(repo))
    assert read.truncated and read.reviewed == 2


def test_an_unreadable_record_is_said_not_counted_as_zero(repo):
    install(repo)
    (repo / ".git" / "agora-review" / "installed").write_text("{not json")
    assert read_review_gate(str(repo)).state == "unreadable"
    for not_an_object in ("null", "[]", '"x"', "3"):
        (repo / ".git" / "agora-review" / "installed").write_text(not_an_object)
        assert read_review_gate(str(repo)).state == "unreadable", not_an_object
    (repo / ".git" / "agora-review" / "installed").write_text(json.dumps({"commit": "f" * 40}))
    read = read_review_gate(str(repo))
    assert read.state == "unreadable" and read.reason
    assert "reviewed" not in read.payload()


def test_a_worktree_finds_the_shared_record(repo, tmp_path):
    install(repo)
    commit(repo, "a.txt", "Reviewed-by: codex")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "wt")
    assert counts(read_review_gate(str(wt)))["reviewed"] == 1


def test_the_workspace_task_payload_carries_it(repo, tmp_path):
    from agora.projects.workspace_tasks import read_workspace_source  # noqa: PLC0415
    install(repo)
    commit(repo, "a.txt", "Reviewed-by: codex")
    work = tmp_path / "work"
    (work / "tasks" / "alpha").mkdir(parents=True)
    (work / "tasks" / "alpha" / "task.yaml").write_text(
        f"task:\n  id: alpha\n  status: active\nprimary_repo: {repo.as_posix()}\n", encoding="utf-8")
    source = read_workspace_source(work)
    gate = source.payload()["tasks"][0]["review_gate"]
    assert gate == {"state": "read", "reviewed": 1, "skipped": 0, "merges": 0, "unrecorded": 0, "truncated": False}


# --- kiro review round 2 -----------------------------------------------------------------

def test_an_install_record_without_a_usable_commit_is_unreadable(repo):
    install(repo)
    for record in ("{}", '{"commit": ""}', '{"commit": "HEAD"}'):
        (repo / ".git" / "agora-review" / "installed").write_text(record)
        assert read_review_gate(str(repo)).state == "unreadable", record


def test_installed_in_an_empty_repository_with_nothing_committed_reads_zero(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    install(r, commit=None)
    assert read_review_gate(str(r)).payload() == {
        "state": "read", "reviewed": 0, "skipped": 0, "merges": 0, "unrecorded": 0, "truncated": False}


def test_a_relative_primary_repo_is_not_guessed(repo):
    assert read_review_gate("..").state == "unreadable"


def test_an_empty_skip_reason_is_not_a_reason(repo):
    install(repo)
    commit(repo, "a.txt", "Review-skipped:")
    assert counts(read_review_gate(str(repo)))["unrecorded"] == 1


def test_a_branch_without_the_install_commit_is_not_counted_from_before_it(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "old")
    commit(repo, "old.txt", patch=None)
    git(repo, "checkout", "-q", "main")
    commit(repo, "main.txt", patch=None)
    install(repo)
    wt = tmp_path / "wt-old"
    git(repo, "worktree", "add", "-q", str(wt), "old")
    read = read_review_gate(str(wt))
    assert read.state == "unreadable" and "does not contain" in (read.reason or "")


def test_a_root_commit_matches_whatever_log_showroot_says(tmp_path):
    r = tmp_path / "rooted"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for key, value in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false"), ("log.showRoot", "false")):
        git(r, "config", key, value)
    install(r, commit=None)
    commit(r, "first.txt", "Reviewed-by: codex")
    assert counts(read_review_gate(str(r)))["reviewed"] == 1


def test_a_marker_that_cannot_be_looked_at_is_unreadable_not_absent(repo, monkeypatch):
    install(repo)
    real_stat = Path.stat

    def denied(self, *a, **k):
        if self.name == "installed":
            raise PermissionError("denied")
        return real_stat(self, *a, **k)
    monkeypatch.setattr(Path, "stat", denied)
    assert read_review_gate(str(repo)).state == "unreadable"


# --- kiro review round 3 -----------------------------------------------------------------

def test_a_git_that_cannot_read_the_repository_is_not_nothing_committed(tmp_path, monkeypatch):
    r = tmp_path / "empty2"
    r.mkdir()
    git(r, "init", "-q")
    install(r, commit=None)
    real = review_gate._git

    def broken(repo, *args, **kw):
        if args[:1] == ("for-each-ref",):
            raise RuntimeError("fatal: detected dubious ownership")
        return real(repo, *args, **kw)
    monkeypatch.setattr(review_gate, "_git", broken)
    assert read_review_gate(str(r)).state == "unreadable"


def test_patch_ids_are_worked_out_in_batches(repo, monkeypatch):
    monkeypatch.setattr(review_gate, "_BATCH", 2)
    install(repo)
    for name in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
        commit(repo, name, "Reviewed-by: codex")
    assert counts(read_review_gate(str(repo)))["reviewed"] == 5
