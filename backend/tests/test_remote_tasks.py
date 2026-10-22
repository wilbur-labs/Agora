"""Remote "open as task" (CR-30 slice 3): which tasks are offered, what opening re-checks,
and what a scan that could not read everything says."""
from __future__ import annotations

import asyncio
import shlex
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from agora.terminal import remote_tasks
from agora.terminal.remote_tasks import (
    RemoteTaskRefused, open_line, scan_remote_tasks, verify_task,
)

TOOL, WORK, ROOT = "/opt/cw", "/data/work", "/srv/proj"


class _Host:
    """A remote host's filesystem: directories, files, symlinks, unreadable files."""

    def __init__(self) -> None:
        self.dirs = {TOOL, f"{TOOL}/.claude", f"{TOOL}/scripts", WORK, f"{WORK}/tasks", ROOT, "/srv/other"}
        self.files: dict[str, str] = {f"{TOOL}/scripts/cw.sh": "#!/bin/bash\n", f"{TOOL}/.workroot": WORK + "\n"}
        self.links: dict[str, str] = {}
        self.denied: set[str] = set()
        self.fs = SimpleNamespace(realpath=self._realpath, read_file=self._read, listdir=self._listdir)

    def task(self, name: str, text: str) -> None:
        self.dirs.add(f"{WORK}/tasks/{name}")
        self.files[f"{WORK}/tasks/{name}/task.yaml"] = text

    def _resolve(self, path: str) -> str:
        for link, target in self.links.items():
            if path == link or path.startswith(link + "/"):
                return target + path[len(link):]
        return path

    async def _realpath(self, path):
        return PurePosixPath(self._resolve(str(path).replace("\\", "/")))

    async def probe(self, path, *, follow_symlinks=True):
        p = str(path).replace("\\", "/")
        return SimpleNamespace(kind="dir" if p in self.dirs else "file" if p in self.files else "absent")

    async def _read(self, path, *, max_bytes=0, truncate=False):
        p = str(path).replace("\\", "/")
        if p in self.denied:
            raise PermissionError(p)
        if p not in self.files:
            raise FileNotFoundError(p)
        return self.files[p].encode("utf-8")

    async def _listdir(self, path):
        p = str(path).replace("\\", "/").rstrip("/")
        return sorted({d[len(p) + 1:].split("/")[0] for d in self.dirs if d.startswith(p + "/")})

    async def run(self, argv, *, cwd=None, timeout, read_only=False):
        out = "abc1234\n" if "rev-parse" in argv else ""
        return SimpleNamespace(outcome="completed", returncode=0, stdout=out)


def _yaml(repo: str, *, status: str = "active", title: str = "T") -> str:
    return f"task:\n  title: {title}\n  status: {status}\nprimary_repo: {repo}\n"


def _scan(host: _Host, tool: str | None = TOOL):
    return asyncio.run(scan_remote_tasks(host, tool, ROOT))


def test_only_tasks_whose_record_names_this_root_are_offered():
    host = _Host()
    host.task("mine", _yaml(ROOT, title="Mine"))
    host.task("other", _yaml("/srv/other"))
    host.task("windows", _yaml("E:/Work/2026/Projects/x"))
    host.task("gone", _yaml(ROOT, status="archived"))
    scan = _scan(host)
    assert scan.state == "complete" and [t.task for t in scan.tasks] == ["mine"]
    assert scan.tasks[0].title == "Mine" and scan.work_root == WORK
    assert scan.remote_repo.head == "abc1234" and scan.remote_repo.dirty is False


def test_a_path_is_compared_where_it_resolves_and_relative_or_dotdot_paths_never_match():
    host = _Host()
    host.links["/srv/jump"] = "/srv/other"
    host.task("via-link", _yaml("/srv/jump"))       # resolves to another directory
    host.task("dotdot", _yaml(f"{ROOT}/sub/.."))    # lexically the root; refused
    host.task("relative", _yaml("proj"))
    host.links["/srv/alias"] = ROOT
    host.task("alias", _yaml("/srv/alias"))         # really the root
    assert [t.task for t in _scan(host).tasks] == ["alias"]


def test_a_record_that_cannot_be_read_makes_the_scan_partial_not_empty():
    host = _Host()
    host.task("mine", _yaml(ROOT))
    host.task("locked", _yaml(ROOT))
    host.denied.add(f"{WORK}/tasks/locked/task.yaml")
    host.task("broken", "task: [unclosed\n")
    scan = _scan(host)
    assert scan.state == "partial" and scan.unreadable == 2 and [t.task for t in scan.tasks] == ["mine"]


def test_no_tool_directory_or_a_wrong_one_is_said_never_shown_as_no_tasks():
    host = _Host()
    assert _scan(host, None).state == "unavailable"
    del host.files[f"{TOOL}/scripts/cw.sh"]
    scan = _scan(host)
    assert scan.state == "unavailable" and "cw.sh" in scan.reason


def test_without_a_workroot_marker_the_tool_is_its_own_work_root():
    host = _Host()
    del host.files[f"{TOOL}/.workroot"]
    host.dirs.add(f"{TOOL}/tasks")
    host.dirs.add(f"{TOOL}/tasks/mine")
    host.files[f"{TOOL}/tasks/mine/task.yaml"] = _yaml(ROOT)
    scan = _scan(host)
    assert scan.work_root == TOOL and [t.task for t in scan.tasks] == ["mine"]


def test_a_host_that_fails_mid_scan_is_unavailable_with_the_reason(monkeypatch):
    host = _Host()

    async def boom(path):
        raise OSError("connection lost")

    host.fs.listdir = boom
    scan = _scan(host)
    assert scan.state == "unavailable" and "OSError" in scan.reason


def test_opening_rechecks_the_task_and_refuses_what_was_not_listed():
    host = _Host()
    host.task("mine", _yaml(ROOT))
    host.task("other", _yaml("/srv/other"))
    tool, work = asyncio.run(verify_task(host, TOOL, ROOT, "mine"))
    assert (str(tool), str(work)) == (TOOL, WORK)
    for task, why in (("other", "没有指向"), ("nope", "没有这个任务"), ("x; rm -rf ~", "不合法")):
        with pytest.raises(RemoteTaskRefused, match=why):
            asyncio.run(verify_task(host, TOOL, ROOT, task))
    host.files[f"{WORK}/tasks/mine/task.yaml"] = _yaml(ROOT, status="archived")  # archived since the menu
    with pytest.raises(RemoteTaskRefused, match="已归档"):
        asyncio.run(verify_task(host, TOOL, ROOT, "mine"))


def test_the_open_line_runs_the_hosts_own_cw_open_with_the_checked_work_root():
    line = open_line(PurePosixPath("/data/my work"), "mine")
    words = shlex.split(line.split(";")[0])
    assert words == ["CW_WORK=/data/my work", "CW_AGENT=claude", "bash", "./scripts/cw.sh", "open", "mine"]
    assert line.endswith('exec "${SHELL:-/bin/sh}" -l')
    with pytest.raises(ValueError):
        open_line(PurePosixPath(WORK), "x;id")


def test_too_many_task_directories_is_partial(monkeypatch):
    monkeypatch.setattr(remote_tasks, "MAX_TASK_DIRS", 2)
    host = _Host()
    for name in ("a", "b", "c"):
        host.task(name, _yaml(ROOT))
    scan = _scan(host)
    assert scan.state == "partial" and scan.truncated and len(scan.tasks) == 2


def test_a_path_that_cannot_be_resolved_is_could_not_tell_not_no_match():
    host = _Host()
    host.task("mine", _yaml("/srv/unreadable"))
    host.dirs.add("/srv/unreadable")
    original = host.probe

    async def probe(path, *, follow_symlinks=True):
        if str(path).replace("\\", "/") == "/srv/unreadable":
            return SimpleNamespace(kind="unknown")
        return await original(path)

    host.probe = probe
    scan = _scan(host)
    assert scan.state == "partial" and scan.unreadable == 1 and scan.tasks == []
    with pytest.raises(RemoteTaskRefused, match="读不了"):
        asyncio.run(verify_task(host, TOOL, ROOT, "mine"))


def test_a_task_directory_whose_name_cannot_be_opened_is_counted_not_dropped():
    host = _Host()
    host.task("日本語 移行", _yaml(ROOT))
    scan = _scan(host)
    assert scan.state == "partial" and scan.unreadable == 1
