"""Read the tasks a claude-workspace / kiro-workspace directory already holds.

W7. The product's domain model came out of claude-workspace — `task.yaml` for the goal,
`RESUME.md` for where it stopped and what is next, a CR table for drift, `adr/` for
decisions — and until now Agora shipped with none of that work in it, only demo rows.
This reads the real thing.

**Live read, not copy.** Nothing here writes to the control-plane database and nothing
writes to the workspace. A copy would need a sync rule, and the two would disagree on
the first edit; a link cannot. It also keeps the product inside `adr/0003`, where an
`Environment` has `run` and `probe` and deliberately no way to write a file.

**Same honesty rules as its siblings** (`cr_log.py`, `decisions.py`,
`progress_snapshot.py`), because they are what make a read worth showing:

* one `probe_path` per stat, and only a definite absence is absence — `Path.exists()`
  answers False both for "nothing there" and for "this process was not allowed to
  look", and collapsing those turns "could not read" into the settled claim "this
  workspace keeps no tasks";
* `tasks` is `None` when the source could not be read, never an empty tuple: a
  registered directory that genuinely holds no task is a different statement from one
  nobody could open, and the UI has to be able to tell them apart;
* a task directory whose `task.yaml` cannot be read is still **listed**, with the reason
  on that field. Dropping it would delete a task from the only place it is visible
  because one of its four files was unreadable;
* fixed relative paths, no searching and no fallback (`adr/0004`'s rule for the CR Log
  and decision records, and the same reason: a reader that goes looking will one day
  find the wrong file and say nothing about it).

Parsing is reused rather than reimplemented: the CR table comes from `cr_log.py`'s
`read_cr_log_file`, the decision records from `decisions.py`'s `read_decisions_dir`, the
进度快照 verdict from `progress_snapshot.py`'s `count_progress_verdict` — the same
reader the project dashboard's progress bar uses, not the marker-only counter, so a
checkbox snapshot is counted rather than read as tracking nothing — and the strict
YAML loader from `importer.py`. Every one of those was already text- or path-level; what
this module adds is the *shape of a workspace* and nothing about how a table is read.
"""

from __future__ import annotations

import re
from agora.projects.review_gate import NOT_INSTALLED, ReviewGateRead, read_review_gate
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path
from agora.projects.cr_log import CRLog, read_cr_log_file, section_scope
from agora.projects.decisions import DecisionLog, read_decisions_dir
from agora.projects.importer import DuplicateKeyError, StrictLoader
from agora.projects.progress_snapshot import ProgressVerdict, count_progress_verdict
from agora.projects.workflow_selection import WorkflowRead, defaults_for, read_workflow

#: Where a workspace keeps its tasks. One fixed name, not a search.
TASKS_DIR = Path("tasks")

#: The three files a task is made of, and its decision directory.
TASK_YAML = "task.yaml"
RESUME_MD = "RESUME.md"
WORKFLOW_MD = "WORKFLOW.md"
ADR_DIR = "adr"

#: `tasks/_template/` belongs to the tool, not to the work: `cw new` copies it. Skipped
#: — and counted, because a directory this module chose not to read is a fact the caller
#: may want, and silence about it would look like the template was never there.
TEMPLATE_DIR_NAME = "_template"

#: A task.yaml is a handful of lines. Anything at this size is not one, and reading it
#: to find that out is the kind of work a UI request should not do.
MAX_TASK_YAML_BYTES = 256 * 1024

#: A RESUME can be long — agora's is thousands of lines — but not unbounded.
MAX_RESUME_BYTES = 4 * 1024 * 1024

#: The section headings this reader looks up inside RESUME.md.
#:
#: **These are identifiers, not prose.** CLAUDE.md names them as such and forbids
#: translating them: the tooling finds sections by these exact strings, so an English
#: "Current state" would simply not be found and the panel would read as though the
#: task had never recorded one.
CURRENT_STATE_HEADING = "当前状态"
NEXT_STEP_HEADING = "下一步"
PROGRESS_HEADING = "进度快照"
#: Where the CR table is. The hook that injects open CRs looks under this heading too.
CR_LOG_HEADING = "变更记录"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


_LINE_END = re.compile(r"\r\n|\r|\n")


def _section(text: str, heading: str) -> str | None:
    """The body under the first heading whose text contains `heading`.

    Contains rather than equals, because these headings carry decoration in practice —
    `## 下一步（按优先级）`, `## 当前状态`, `### 进度快照` — and the identifier is the
    part CLAUDE.md fixes. Returns None when no heading matches, which is a different
    answer from an empty body: a task that records no next step and a file this reader
    could not find the section in must stay distinguishable.
    """
    # Line ends only. `splitlines()` also breaks on a vertical tab and the Unicode
    # separators, and rejoining with newlines then turns one line holding such a
    # character into two, which breaks a table row or a list item in the section.
    lines = _LINE_END.split(text)
    start: int | None = None
    level = 0
    for n, line in enumerate(lines):
        m = _HEADING.match(line)
        if m is None:
            continue
        if start is None:
            if heading in m.group(2):
                start, level = n + 1, len(m.group(1))
            continue
        # The section ends at the next heading of the same or higher rank. A deeper one
        # is part of it — 进度快照 sits under 当前状态 in these files.
        if len(m.group(1)) <= level:
            return "\n".join(lines[start:n]).strip()
    if start is None:
        return None
    return "\n".join(lines[start:]).strip()


@dataclass(frozen=True)
class TaskManifestRead:
    """What `task.yaml` said, or why it did not."""

    available: bool
    #: "absent" | "unreadable" | "too_large" | "malformed" | "duplicate_key"
    #: | "unknown_schema"
    #:
    #: `unknown_schema` is its own reason and not folded into `malformed`, because the
    #: two send the reader to different places. A malformed file has a syntax error to
    #: find; a file with no `task:` block is very likely **another tool's** task.yaml —
    #: kiro-workspace writes `name` / `project_path` / `deliverable` / `output_dir` at
    #: the top level and no `task:` at all. Reporting that as malformed would send
    #: someone hunting a broken colon in a file that is perfectly well formed.
    #:
    #: Found by pointing this reader at a real kiro-workspace, after claiming in the
    #: task record that the two workspaces were "the same shape" — a claim made from
    #: the four filenames matching, without opening one. Same filenames, different
    #: schemas.
    reason: str | None
    title: str | None
    goal: str | None
    status: str | None
    machine: str | None
    #: `primary_repo`, verbatim from the file. The repository this task's work happens
    #: in, which the working model has always recorded and which nothing read until now.
    #: None both when the manifest could not be read and when the file does not name one
    #: — `available` is what tells those apart.
    primary_repo: str | None
    #: True when the key is there and its value is not a usable path — `primary_repo: 123`,
    #: a list, or an empty string. Without this the value would fall to None and the panel
    #: would say "no primary_repo recorded" about a file that records one and needs it
    #: fixed: two different things to do, one sentence.
    primary_repo_malformed: bool
    #: Milestone ids and their statuses, in file order. None when unavailable.
    milestones: tuple[tuple[str, str], ...] | None
    #: The `workflow:` block (CR-75 slice 4). None when the manifest is unavailable.
    workflow: WorkflowRead | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "machine": self.machine,
            "primary_repo": self.primary_repo,
            "primary_repo_malformed": self.primary_repo_malformed,
            "milestones": (
                None if self.milestones is None
                else [{"id": i, "status": s} for i, s in self.milestones]
            ),
            "workflow": None if self.workflow is None else self.workflow.payload(),
        }


@dataclass(frozen=True)
class ResumeRead:
    """What `RESUME.md` said, or why it did not.

    `current_state` and `next_step` are None both when the file could not be read and
    when the section is not in it; `reason` is what tells those apart, and a caller that
    renders the text without consulting it would present a missing section as an empty
    one.
    """

    available: bool
    #: "absent" | "unreadable" | "too_large"
    reason: str | None
    current_state: str | None
    next_step: str | None
    #: The 进度快照 section's verdict, or None when the section is not there.
    #:
    #: The same reader the project dashboard's progress bar uses
    #: (`progress_snapshot.count_progress_verdict`), *not* the marker-only counter it
    #: called before: that one knows ✅/🔵/⬜ alone, so a snapshot written with `- [x]`
    #: checkboxes — which is how agora's own is written — came back three zeros and read
    #: as "tracks nothing yet". A verdict carries the counts, the `convention` they were
    #: read in, and, when the section was read but cannot be counted, the `reason`
    #: (`mixed_conventions` / `uncounted_markers`) — so the UI can tell "read fine but
    #: uncountable" from "empty" from "the RESUME itself was unreadable". None here means
    #: only that the file has no 进度快照 section; an empty dict never happens.
    progress: ProgressVerdict | None

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "current_state": self.current_state,
            "next_step": self.next_step,
            # The verdict flattened: counts plus the convention they were read in, or
            # the reason the section could not be counted. None stays None — no section.
            "progress": None if self.progress is None else {
                "done": self.progress.done,
                "in_progress": self.progress.in_progress,
                "todo": self.progress.todo,
                "convention": self.progress.convention,
                "reason": self.progress.reason,
            },
        }


class ProjectsUnreadable:
    """Sentinel: the project list could not be read at all.

    A third thing, distinct from a mapping (looked, here is what there is) and from None
    (nobody looked). Folding it into either was the review's finding: as `{}` every task
    reads "that repository is not registered", a claim about projects the request never
    saw; as None it reads `unknown`, which the panel renders as nothing at all — the
    links a person had a moment ago simply vanish with nothing on screen saying why.
    """

    __slots__ = ()


#: The one instance callers pass. A sentinel rather than a second boolean parameter, so
#: there is no pair of arguments that can disagree.
PROJECTS_UNREADABLE = ProjectsUnreadable()

ProjectRoots = Mapping[str, Path] | None | ProjectsUnreadable


def _normalised(path: Path) -> Path:
    """The path as the filesystem sees it, when it can be asked.

    `Path.__eq__` is lexical: it does not follow a junction or a symlink and does not
    fold `..`, so `D:/Projects/A/../Agora` and a link to the real directory compare
    unequal to it. Project roots are stored already resolved (`ProjectRegistry.add`),
    while `task.yaml` holds whatever the person typed — so without this the two sides
    are normalised differently and the answer becomes "not registered" about a
    repository that is registered.

    Falls back to the lexical path when resolution cannot happen (offline share, a
    permission the process lacks): a comparison that could not be normalised is still
    worth making, and it is the same answer this had before.
    """
    try:
        return path.resolve()
    except OSError:
        return path


@dataclass(frozen=True)
class RepoLink:
    """Whether this task's repository is one Agora already knows, and how sure that is.

    Six states, because each one is a different next move: open it / register that
    directory / write `primary_repo` into the task / fix the value that is there / try
    again once the project list can be read / say nothing at all. Collapsing any pair of
    them recommends the wrong one, and two of these exist because a review found exactly
    that collapse.

    **This is a lookup, not a key.** `Project.root` carries an explicit warning that
    nothing may key, join or dedupe on it, because a project's root is its location in
    the *local* environment and moves into a per-environment table when W3 lands.
    Nothing here is stored, nothing is decided by it, and no identity is derived from
    it: one function turns a path into a candidate `project_id` for a link the person
    may click, and when roots stop living on `Project` this is the one place that has
    to change. `project_id` remains the only key. Recorded in tasks/agora/adr/0006.
    """

    #: "linked" | "not_registered" | "not_recorded" | "repo_malformed"
    #: | "projects_unreadable" | "unknown"
    #:
    #: `not_recorded` — the manifest was read and names no `primary_repo`.
    #: `repo_malformed` — it names one whose value is not a usable path.
    #: `not_registered` — it names one, and no registered project sits at that path.
    #: `projects_unreadable` — the task read fine; the project list did not.
    #: `unknown` — nobody looked, or the manifest could not be read at all. Never say
    #: "not registered" about a file you could not open.
    state: str
    #: The path as the task file wrote it. None unless the manifest was read and had one.
    repo: str | None
    #: Only ever set on "linked".
    project_id: str | None

    def payload(self) -> dict[str, Any]:
        return {"state": self.state, "repo": self.repo, "project_id": self.project_id}


def link_repo(manifest: TaskManifestRead, project_roots: ProjectRoots) -> RepoLink:
    """Match a task's `primary_repo` against the roots of registered projects.

    Compared as normalised paths: `Path.__eq__` gets Windows case and separators right,
    and `_normalised` gets junctions, symlinks and `..` right. String comparison gets
    neither — the same defect the registration path had once.
    """
    if not manifest.available:
        return RepoLink(state="unknown", repo=None, project_id=None)
    if manifest.primary_repo_malformed:
        return RepoLink(state="repo_malformed", repo=None, project_id=None)
    if manifest.primary_repo is None:
        return RepoLink(state="not_recorded", repo=None, project_id=None)
    if isinstance(project_roots, ProjectsUnreadable):
        return RepoLink(state="projects_unreadable", repo=manifest.primary_repo, project_id=None)
    if project_roots is None:
        return RepoLink(state="unknown", repo=manifest.primary_repo, project_id=None)

    wanted = _normalised(Path(manifest.primary_repo).expanduser())
    for project_id, root in project_roots.items():
        if _normalised(Path(root)) == wanted:
            return RepoLink(state="linked", repo=manifest.primary_repo, project_id=project_id)
    return RepoLink(state="not_registered", repo=manifest.primary_repo, project_id=None)


@dataclass(frozen=True)
class WorkspaceTask:
    """One `tasks/<name>/` directory, whatever of it could be read."""

    name: str
    manifest: TaskManifestRead
    resume: ResumeRead
    #: The CR table embedded in RESUME.md, read by the same parser as a project's
    #: `.agora/development/CR-LOG.md`.
    cr_log: CRLog
    #: `tasks/<name>/adr/`, read by the same parser as `.agora/decisions/`.
    decisions: DecisionLog
    #: Whether `WORKFLOW.md` is there. Not parsed — the hook shows its first 40 lines and
    #: nothing here needs its content, so this says only that it exists.
    has_workflow: bool
    #: Where this task's work happens, if Agora knows that place. See `RepoLink`.
    repo_link: RepoLink
    #: The commit review gate's record in `primary_repo` (CR-75 slice 2). See
    #: `review_gate.py`; "not_installed" also when no repository is named.
    review_gate: ReviewGateRead = NOT_INSTALLED

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "manifest": self.manifest.payload(),
            "resume": self.resume.payload(),
            "cr_log": self.cr_log.payload(),
            "decisions": self.decisions.payload(),
            "has_workflow": self.has_workflow,
            "repo_link": self.repo_link.payload(),
            "review_gate": self.review_gate.payload(),
        }


@dataclass(frozen=True)
class WorkspaceSource:
    """One registered workspace directory, or the reason it has nothing to show."""

    available: bool
    #: "absent" | "unreadable" | "not_a_directory" | "no_tasks_dir"
    #:
    #: `no_tasks_dir` is deliberately its own reason and not folded into "absent": a
    #: path that exists but keeps no `tasks/` is very likely the wrong directory — the
    #: tool's parent, say — and telling the person that is more useful than telling them
    #: it is empty.
    #:
    #: `unreadable` covers the third case, and it took a review round to keep it: a
    #: probe that could not stat the path answers `unknown`, and the first version read
    #: `probe.is_dir()` as a boolean, so an unreachable network share or a directory
    #: this process may not stat came back as `not_a_directory` — or, one level down, as
    #: `no_tasks_dir`. Both send the person to pick a different folder for a folder that
    #: is fine. `PathProbe` is tri-state precisely so this cannot happen; using it as a
    #: boolean threw the third state away.
    reason: str | None
    root: str
    #: None when unavailable. Empty tuple means: this workspace has a `tasks/` directory
    #: and nothing in it, which is a real and different answer.
    tasks: tuple[WorkspaceTask, ...] | None
    #: Directories under `tasks/` that were skipped on purpose, named so that nothing
    #: vanishes silently. Today that is `_template` only.
    skipped: tuple[str, ...] | None
    #: Entries under `tasks/` that could not be stat'd, named for the same reason.
    #:
    #: The first version filtered the listing with `Path.is_dir()`, so an entry that
    #: could not be stat'd was dropped from `tasks`, dropped from `skipped`, and the
    #: source still reported success — a task deleted from the only place it is visible,
    #: with nothing anywhere saying so. That is the exact collapse this module's own
    #: docstring warns about, made one line below it.
    unreadable_entries: tuple[str, ...] | None

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "root": self.root,
            "tasks": None if self.tasks is None else [t.payload() for t in self.tasks],
            "skipped": None if self.skipped is None else list(self.skipped),
            "unreadable_entries": (
                None if self.unreadable_entries is None else list(self.unreadable_entries)
            ),
        }


def _read_manifest(path: Path, *, workspace: str | None = "claude") -> TaskManifestRead:
    """``workspace`` (``claude`` / ``kiro``) picks the workflow switches' defaults; None —
    a workspace whose sessions are not known to read the selection — carries no workflow."""
    unavailable = lambda reason, workflow=None: TaskManifestRead(  # noqa: E731 - one shape, many exits
        available=False, reason=reason, title=None, goal=None,
        status=None, machine=None, primary_repo=None, primary_repo_malformed=False,
        milestones=None, workflow=workflow,
    )
    probe = probe_path(path)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_file():
        return unavailable("unreadable")
    if probe.size is not None and probe.size > MAX_TASK_YAML_BYTES:
        return unavailable("too_large")
    try:
        # Bytes, not read_text: newline translation would hash a CRLF file as LF, and the
        # workflow edit (which hashes the bytes) would then refuse every change as "changed".
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return unavailable("unreadable")
    try:
        # StrictLoader, shared with the project importer: a duplicated `goal:` would
        # otherwise parse as the last one and be shown as *the* goal, with nothing
        # saying a second existed.
        document = yaml.load(text, Loader=StrictLoader)
    except DuplicateKeyError:
        return unavailable("duplicate_key")
    except yaml.YAMLError:
        return unavailable("malformed")
    if not isinstance(document, dict):
        return unavailable("malformed")

    if "task" not in document:
        # Parsed cleanly and simply is not this shape. A kiro-workspace task.yaml is
        # exactly that, and its `workflow:` block is still read: the kiro agent loads the
        # file and follows the block (CR-75 slice 4, kiro).
        return unavailable("unknown_schema",
                           read_workflow(document, text, defaults_for("kiro")) if workspace == "kiro" else None)
    task = document.get("task")
    if not isinstance(task, dict):
        return unavailable("malformed")

    def text_of(value: Any) -> str | None:
        # An empty `goal: ""` is how `cw start` leaves a task nobody has interviewed
        # yet, and CLAUDE.md keys /kickoff off exactly that. So empty is reported as
        # absent-of-content (None) rather than as the string "", which a UI would
        # render as a goal that happens to be blank.
        return value.strip() if isinstance(value, str) and value.strip() else None

    milestones: list[tuple[str, str]] = []
    raw_milestones = document.get("milestones")
    if isinstance(raw_milestones, list):
        for item in raw_milestones:
            if not isinstance(item, dict):
                continue
            mid, mstatus = item.get("id"), item.get("status")
            if isinstance(mid, str) and mid.strip():
                milestones.append((mid.strip(), str(mstatus).strip() if mstatus else ""))

    return TaskManifestRead(
        available=True,
        reason=None,
        title=text_of(task.get("title")),
        goal=text_of(task.get("goal")),
        status=text_of(task.get("status")),
        machine=text_of(task.get("machine")),
        # Top level, not under `task:` — that is where the working model writes
        # it, and this reader follows the file rather than tidying it.
        primary_repo=text_of(document.get("primary_repo")),
        primary_repo_malformed=(
            "primary_repo" in document and text_of(document.get("primary_repo")) is None
        ),
        milestones=tuple(milestones),
        workflow=read_workflow(document, text, defaults_for(workspace)) if workspace else None,
    )


def _read_resume(path: Path) -> ResumeRead:
    unavailable = lambda reason: ResumeRead(  # noqa: E731 - one shape, several exits
        available=False, reason=reason, current_state=None, next_step=None, progress=None,
    )
    probe = probe_path(path)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_file():
        return unavailable("unreadable")
    if probe.size is not None and probe.size > MAX_RESUME_BYTES:
        return unavailable("too_large")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return unavailable("unreadable")

    progress_section = _section(text, PROGRESS_HEADING)
    return ResumeRead(
        available=True,
        reason=None,
        current_state=_section(text, CURRENT_STATE_HEADING),
        next_step=_section(text, NEXT_STEP_HEADING),
        # The same verdict the dashboard's progress bar is driven by, on the section's
        # own text — counts, the convention they were read in, and the reason when the
        # section was read but cannot be counted. None only when there is no section.
        progress=None if progress_section is None else count_progress_verdict(progress_section),
    )


#: Where a kiro-workspace explains the `workflow:` block to its agents (CR-75 slice 4, kiro).
KIRO_WORKFLOW_STEERING = Path(".kiro") / "steering" / "workflow-selection.md"


def workflow_workspace(root: Path) -> str | None:
    """Which workspace's sessions read a task's `workflow:` block here: ``claude``, ``kiro``,
    or None when that is not established — nothing is then offered or claimed.

    The terminal targets' test (agora/terminal/targets.py `_is_kiro`: `.kiro/` and no
    `.claude/`), with an unknown probe kept unknown rather than read as absent; and a
    kiro-workspace counts only when it has the steering file that tells kiro what the
    block means — an older kiro-workspace would ignore every switch (kiro review)."""
    kiro, claude = probe_path(root / ".kiro").is_dir(), probe_path(root / ".claude").is_dir()
    if kiro is None or claude is None:
        return None
    if not kiro or claude:
        return "claude"
    return "kiro" if probe_path(root / KIRO_WORKFLOW_STEERING).is_file() is True else None


def read_workspace_source(
    root: Path, *, project_roots: ProjectRoots = None
) -> WorkspaceSource:
    """Read one workspace directory. Never raises — every failure is a reason to show.

    `project_roots` is `{project_id: root}` for the projects Agora has registered, used
    only to decide whether a task's `primary_repo` is somewhere the person can already
    open. Omit it and every task reports `unknown` rather than "not registered": a
    caller that did not look has not established an absence.
    """
    shown_root = root.as_posix()
    unavailable = lambda reason: WorkspaceSource(  # noqa: E731 - one shape, several exits
        available=False, reason=reason, root=shown_root, tasks=None, skipped=None,
        unreadable_entries=None,
    )

    # `is_dir()` is tri-state here: None means the stat itself did not succeed. Each of
    # the three answers gets its own reason, because "pick another folder" is the wrong
    # advice for a share that is merely offline.
    probe = probe_path(root)
    if probe.absent:
        return unavailable("absent")
    if probe.is_dir() is None:
        return unavailable("unreadable")
    if not probe.is_dir():
        return unavailable("not_a_directory")

    tasks_dir = root / TASKS_DIR
    tasks_probe = probe_path(tasks_dir)
    if tasks_probe.absent:
        return unavailable("no_tasks_dir")
    if tasks_probe.is_dir() is None:
        return unavailable("unreadable")
    if not tasks_probe.is_dir():
        return unavailable("no_tasks_dir")

    try:
        entries = sorted(tasks_dir.iterdir())
    except OSError:
        return unavailable("unreadable")

    workspace = workflow_workspace(root)

    tasks: list[WorkspaceTask] = []
    skipped: list[str] = []
    unreadable_entries: list[str] = []
    for entry in entries:
        # Probed, not `is_dir()`-filtered. An entry the listing produced but nobody can
        # stat is neither a task nor "not a directory" — it is unknown, and it has to be
        # named rather than filtered away.
        entry_probe = probe_path(entry)
        entry_is_dir = entry_probe.is_dir()
        if entry_is_dir is None:
            unreadable_entries.append(entry.name)
            continue
        if not entry_is_dir:
            continue
        if entry.name == TEMPLATE_DIR_NAME:
            skipped.append(entry.name)
            continue
        resume_path = entry / RESUME_MD
        manifest = _read_manifest(entry / TASK_YAML, workspace=workspace)
        tasks.append(WorkspaceTask(
            name=entry.name,
            manifest=manifest,
            resume=_read_resume(resume_path),
            # The CR table lives inside RESUME.md here, not in a file of its own, which
            # is the whole reason `read_cr_log_file` takes a path.
            cr_log=read_cr_log_file(
                resume_path, (TASKS_DIR / entry.name / RESUME_MD).as_posix(),
                # ... and a RESUME holds other tables that name CRs, so only the log's
                # own section is read as the log.
                scope=section_scope(CR_LOG_HEADING),
            ),
            decisions=read_decisions_dir(
                entry / ADR_DIR, (TASKS_DIR / entry.name / ADR_DIR).as_posix()
            ),
            has_workflow=probe_path(entry / WORKFLOW_MD).is_file(),
            repo_link=link_repo(manifest, project_roots),
            review_gate=read_review_gate(
                manifest.primary_repo
                if manifest.available and not manifest.primary_repo_malformed else None
            ),
        ))

    return WorkspaceSource(
        available=True, reason=None, root=shown_root,
        tasks=tuple(tasks), skipped=tuple(skipped),
        unreadable_entries=tuple(unreadable_entries),
    )
