"""Reading the tasks an existing claude-workspace directory already holds (W7).

What these pin is the same thing every reader in this package has to keep true: the
difference between *nothing is there* and *I could not look*. A workspace source has
four ways of yielding nothing (absent / unreadable / not a directory / no `tasks/`) and
they send the person to four different places — create one, fix a permission, pick a
folder instead of a file, pick a different folder — so none of them may arrive as an
empty list.

Two of these tests exist because of a claim that was written down before it was
measured. The task record said the two workspaces were "the same shape", from the four
filenames matching and without opening one; kiro-workspace's `task.yaml` has no `task:`
block at all. `unknown_schema` is therefore its own answer and not folded into
`malformed`: a malformed file has a syntax error to hunt, another tool's file has none.

And one pins the acceptance line itself: reading a workspace **writes nothing**,
measured against the tree rather than promised in a docstring.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agora.projects.workspace_tasks import (
    PROJECTS_UNREADABLE,
    link_repo,
    MAX_RESUME_BYTES,
    MAX_TASK_YAML_BYTES,
    TEMPLATE_DIR_NAME,
    read_workspace_source,
)

RESUME_HEADER = (
    "| ID | 日期 | 来源 | 描述 | 类型 | 状态 | 决定 | 已传播到 |\n"
    "|---|---|---|---|---|---|---|---|\n"
)

TASK_YAML = """\
task:
  id: alpha
  title: Alpha — 一个真实任务
  goal: 把工作模型产品化
  status: active
  machine: windows

milestones:
  - id: W1
    title: 第一刀
    status: done
  - id: W2
    title: 第二刀
    status: planned
"""

RESUME_MD = """\
# RESUME — alpha

## 项目目标 / ANCHOR

锚点段落。

## 当前状态

停在 W2 的入口。

### 进度快照

- ✅ W1 完成
- 🔵 W2 进行中
- ⬜ W3 未开工

## 下一步（按优先级）

先打通，再加固。

## 变更记录 (CR Log)

""" + RESUME_HEADER + (
    "| CR-1 | 2026-09-06 | user | 描述 | refine | DONE | 决定 | 已传播 |\n"
    "| CR-2 | 2026-09-06 | agent | 描述 | defer | OPEN | 决定 | 触发物 |\n"
)


def _task(root: Path, name: str, *, manifest: str | None = TASK_YAML,
          resume: str | None = RESUME_MD, workflow: str | None = "# WORKFLOW\n") -> Path:
    """One `tasks/<name>/` directory, with whichever of its files the test wants."""
    directory = root / "tasks" / name
    directory.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (directory / "task.yaml").write_text(manifest, encoding="utf-8")
    if resume is not None:
        (directory / "RESUME.md").write_text(resume, encoding="utf-8")
    if workflow is not None:
        (directory / "WORKFLOW.md").write_text(workflow, encoding="utf-8")
    return directory


def _refuse_lstat(monkeypatch, targets: set[Path], error: OSError | None = None) -> None:
    """Make `lstat` fail for exactly these paths, and behave normally for the rest.

    Selective on purpose: a blanket refusal cannot tell "the entry could not be
    stat'd" from "the whole tree could not be", and those are the two answers under
    test.
    """
    real = Path.lstat
    wanted = {str(t) for t in targets}

    def guarded(self, *args, **kwargs):
        if str(self) in wanted:
            raise error or PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded)


def _fingerprint(root: Path) -> set[tuple[str, str]]:
    """Every path under `root` and the digest of its bytes. Used to prove a read wrote
    nothing — measured against the tree, not asserted about the code."""
    seen: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            seen.add((relative, "<dir>"))
        else:
            seen.add((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return seen


# --- the source: four ways to yield nothing, and none of them is an empty list ----


def test_a_source_that_is_not_there_is_absent_and_lists_no_tasks(tmp_path):
    source = read_workspace_source(tmp_path / "gone")

    assert source.available is False
    assert source.reason == "absent"
    # None, not (): "this directory is not there" must not render as "it holds no work".
    assert source.tasks is None
    assert source.skipped is None
    assert source.unreadable_entries is None


def test_a_file_given_as_a_workspace_says_so_rather_than_saying_it_is_empty(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("not a workspace\n", encoding="utf-8")

    source = read_workspace_source(path)

    assert (source.available, source.reason) == (False, "not_a_directory")


def test_a_directory_with_no_tasks_dir_is_its_own_answer(tmp_path):
    """`no_tasks_dir` rather than `absent`: a directory that exists but keeps no
    `tasks/` is very likely the wrong folder, and saying "empty" hides that."""
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")

    source = read_workspace_source(tmp_path)

    assert (source.available, source.reason) == (False, "no_tasks_dir")
    assert source.tasks is None


def test_a_file_named_tasks_is_no_tasks_dir_not_a_readable_workspace(tmp_path):
    (tmp_path / "tasks").write_text("not a directory\n", encoding="utf-8")

    assert read_workspace_source(tmp_path).reason == "no_tasks_dir"


def test_a_root_that_cannot_be_stat_ed_is_unreadable_not_not_a_directory(tmp_path, monkeypatch):
    """The tri-state case review caught: reading `probe.is_dir()` as a boolean turned
    an unreachable share into "that is not a directory", which sends the person to pick
    another folder for a folder that is fine."""
    _task(tmp_path, "alpha")
    _refuse_lstat(monkeypatch, {tmp_path})

    source = read_workspace_source(tmp_path)

    assert (source.available, source.reason) == (False, "unreadable")


def test_a_tasks_dir_that_cannot_be_stat_ed_is_unreadable_not_no_tasks_dir(tmp_path, monkeypatch):
    _task(tmp_path, "alpha")
    _refuse_lstat(monkeypatch, {tmp_path / "tasks"})

    assert read_workspace_source(tmp_path).reason == "unreadable"


def test_a_tasks_dir_that_cannot_be_listed_is_unreadable(tmp_path, monkeypatch):
    _task(tmp_path, "alpha")
    real = Path.iterdir

    def refuse(self):
        if self == tmp_path / "tasks":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", refuse)

    assert read_workspace_source(tmp_path).reason == "unreadable"


def test_an_empty_tasks_dir_is_available_with_no_tasks(tmp_path):
    """The one case that legitimately yields no tasks — and it is `()`, not `None`."""
    (tmp_path / "tasks").mkdir()

    source = read_workspace_source(tmp_path)

    assert (source.available, source.reason) == (True, None)
    assert source.tasks == ()
    assert source.skipped == ()
    assert source.unreadable_entries == ()


# --- what the listing may and may not drop ---------------------------------------


def test_tasks_are_listed_by_name_in_order(tmp_path):
    for name in ("kimono", "agora", "obsidian-vault"):
        _task(tmp_path, name)

    source = read_workspace_source(tmp_path)

    assert [t.name for t in source.tasks] == ["agora", "kimono", "obsidian-vault"]


def test_the_template_directory_is_skipped_and_named(tmp_path):
    """`tasks/_template/` belongs to the tool, not to the work — but a directory this
    reader chose not to read is still a fact, so it is counted rather than vanished."""
    _task(tmp_path, "alpha")
    _task(tmp_path, TEMPLATE_DIR_NAME)

    source = read_workspace_source(tmp_path)

    assert [t.name for t in source.tasks] == ["alpha"]
    assert source.skipped == (TEMPLATE_DIR_NAME,)


def test_an_entry_that_cannot_be_stat_ed_is_named_not_filtered_away(tmp_path, monkeypatch):
    """The defect this replaces: filtering the listing with `is_dir()` dropped an
    unstattable entry from `tasks`, from `skipped`, and reported success — a task
    deleted from the only place it is visible, with nothing saying so."""
    _task(tmp_path, "alpha")
    _task(tmp_path, "beta")
    _refuse_lstat(monkeypatch, {tmp_path / "tasks" / "beta"})

    source = read_workspace_source(tmp_path)

    assert source.available is True
    assert [t.name for t in source.tasks] == ["alpha"]
    assert source.unreadable_entries == ("beta",)


def test_a_stray_file_under_tasks_is_not_a_task_and_not_an_unreadable_entry(tmp_path):
    _task(tmp_path, "alpha")
    (tmp_path / "tasks" / "notes.md").write_text("stray\n", encoding="utf-8")

    source = read_workspace_source(tmp_path)

    assert [t.name for t in source.tasks] == ["alpha"]
    assert source.unreadable_entries == ()


# --- task.yaml ---------------------------------------------------------------------


def test_a_task_manifest_is_read_field_by_field(tmp_path):
    _task(tmp_path, "alpha")

    task = read_workspace_source(tmp_path).tasks[0]

    assert task.manifest.available is True
    assert task.manifest.reason is None
    assert task.manifest.title == "Alpha — 一个真实任务"
    assert task.manifest.goal == "把工作模型产品化"
    assert task.manifest.status == "active"
    assert task.manifest.machine == "windows"
    assert task.manifest.milestones == (("W1", "done"), ("W2", "planned"))
    assert task.has_workflow is True


def test_a_task_with_no_manifest_is_still_listed(tmp_path):
    """Dropping it would delete a task from the only place it is visible because one
    of its four files is missing."""
    _task(tmp_path, "alpha", manifest=None)

    task = read_workspace_source(tmp_path).tasks[0]

    assert task.name == "alpha"
    assert (task.manifest.available, task.manifest.reason) == (False, "absent")
    assert task.manifest.goal is None
    assert task.manifest.milestones is None


def test_another_tools_task_yaml_is_unknown_schema_not_malformed(tmp_path):
    """kiro-workspace's shape, measured rather than assumed: `name` / `project_path` /
    `deliverable` / `output_dir` at the top level and no `task:` block.

    Reporting this as `malformed` sends someone hunting a broken colon in a file that
    parses perfectly. The two answers lead to different places, so they stay apart.
    """
    _task(tmp_path, "stm32", manifest=(
        "name: stm32f407-manual\n"
        "project_path: D:/Projects/stm32\n"
        "deliverable: 手册\n"
        "output_dir: out\n"
    ))

    task = read_workspace_source(tmp_path).tasks[0]

    assert (task.manifest.available, task.manifest.reason) == (False, "unknown_schema")


def test_a_manifest_that_is_not_a_mapping_is_malformed(tmp_path):
    _task(tmp_path, "alpha", manifest="- just\n- a list\n")

    assert read_workspace_source(tmp_path).tasks[0].manifest.reason == "malformed"


def test_a_task_block_that_is_not_a_mapping_is_malformed(tmp_path):
    _task(tmp_path, "alpha", manifest="task: just a string\n")

    assert read_workspace_source(tmp_path).tasks[0].manifest.reason == "malformed"


def test_broken_yaml_is_malformed(tmp_path):
    _task(tmp_path, "alpha", manifest="task:\n  goal: [unclosed\n")

    assert read_workspace_source(tmp_path).tasks[0].manifest.reason == "malformed"


def test_a_duplicated_key_is_its_own_reason_and_no_goal_is_shown(tmp_path):
    """Plain `yaml.safe_load` keeps the last of two `goal:` keys and shows it as *the*
    goal with nothing saying a second existed. The strict loader is shared with the
    project importer for exactly that reason."""
    _task(tmp_path, "alpha", manifest=(
        "task:\n"
        "  id: alpha\n"
        "  goal: first goal\n"
        "  goal: second goal\n"
    ))

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert (manifest.available, manifest.reason) == (False, "duplicate_key")
    assert manifest.goal is None


def test_a_complex_key_is_malformed_and_does_not_escape_as_an_exception(tmp_path):
    """`? [a, b]` is legal YAML this reader does not support, and it used to leave the
    module by a route no caller was watching.

    The strict loader replaced PyYAML's `construct_mapping` and dropped its hash check,
    so an unhashable key reached `key in mapping` and raised a bare `TypeError` — not a
    `yaml.YAMLError`, therefore not caught here, therefore a 500 on
    `/api/workspace-sources` that takes *every* registered source down over one file in
    one task. Fixed at the loader, where both readers of it benefit.
    """
    _task(tmp_path, "alpha", manifest="task:\n  ? [a, b]\n  : v\n")

    source = read_workspace_source(tmp_path)

    assert source.available is True
    assert source.tasks[0].manifest.reason == "malformed"


def test_an_empty_goal_is_reported_as_no_goal_not_as_a_blank_one(tmp_path):
    """`cw start` leaves `goal: ""` for a task nobody has interviewed yet, and that is
    what /kickoff keys off. The file is readable; the goal is not there yet."""
    _task(tmp_path, "alpha", manifest='task:\n  id: alpha\n  goal: "   "\n')

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert manifest.available is True
    assert manifest.goal is None


def test_a_manifest_past_the_size_bound_is_too_large_not_malformed(tmp_path):
    _task(tmp_path, "alpha", manifest="task:\n  goal: g\n# " + "x" * MAX_TASK_YAML_BYTES)

    assert read_workspace_source(tmp_path).tasks[0].manifest.reason == "too_large"


def test_a_directory_where_task_yaml_should_be_is_unreadable(tmp_path):
    directory = _task(tmp_path, "alpha", manifest=None)
    (directory / "task.yaml").mkdir()

    assert read_workspace_source(tmp_path).tasks[0].manifest.reason == "unreadable"


def test_milestone_entries_that_are_not_mappings_are_skipped_and_the_rest_kept(tmp_path):
    _task(tmp_path, "alpha", manifest=(
        "task:\n  id: alpha\n"
        "milestones:\n"
        "  - W-not-a-mapping\n"
        "  - id: W1\n    status: done\n"
        "  - title: no id at all\n"
        "  - id: W2\n"
    ))

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    # A milestone with no status is listed with an empty one — it is a milestone that
    # exists, which is not the same as a milestone that is not there.
    assert manifest.milestones == (("W1", "done"), ("W2", ""))


def test_no_milestones_key_yields_an_empty_tuple_on_a_readable_manifest(tmp_path):
    _task(tmp_path, "alpha", manifest="task:\n  id: alpha\n  goal: g\n")

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert manifest.available is True
    assert manifest.milestones == ()


# --- the repository a task names, and whether Agora knows it -------------------------


REPO_YAML = "task:\n  id: alpha\n  goal: g\nprimary_repo: D:/Projects/Alpha\n"


def test_primary_repo_is_read_from_the_top_level_where_the_model_writes_it(tmp_path):
    _task(tmp_path, "alpha", manifest=REPO_YAML)

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert manifest.primary_repo == "D:/Projects/Alpha"


def test_a_task_whose_repo_is_a_registered_project_links_to_it(tmp_path):
    _task(tmp_path, "alpha", manifest=REPO_YAML)

    source = read_workspace_source(
        tmp_path, project_roots={"alpha-repo": Path("D:/Projects/Alpha")}
    )

    link = source.tasks[0].repo_link
    assert (link.state, link.project_id) == ("linked", "alpha-repo")
    assert link.repo == "D:/Projects/Alpha"


def test_the_repo_match_uses_path_rules_not_string_equality(tmp_path):
    """`D:/Projects/Alpha` and `D:\\Projects\\Alpha` are one directory. Comparing the
    two as strings is the defect the registration path already had once."""
    _task(tmp_path, "alpha", manifest=REPO_YAML)

    source = read_workspace_source(
        tmp_path, project_roots={"alpha-repo": Path("D:\\Projects\\Alpha")}
    )

    assert source.tasks[0].repo_link.state == "linked"


def test_a_repo_no_project_sits_at_is_not_registered_not_unknown(tmp_path):
    _task(tmp_path, "alpha", manifest=REPO_YAML)

    source = read_workspace_source(tmp_path, project_roots={"other": Path("D:/Projects/Other")})

    link = source.tasks[0].repo_link
    assert (link.state, link.project_id) == ("not_registered", None)
    # The path still travels: the next move is "register that directory", and the person
    # needs to know which one.
    assert link.repo == "D:/Projects/Alpha"


def test_a_manifest_with_no_primary_repo_says_so_rather_than_not_registered(tmp_path):
    """Read the file, it names no repository. That is a different sentence from "the
    repository it names is not registered", and a different thing to do about it."""
    _task(tmp_path, "alpha")  # the default manifest has no primary_repo

    source = read_workspace_source(tmp_path, project_roots={"other": Path("D:/x")})

    link = source.tasks[0].repo_link
    assert (link.state, link.repo, link.project_id) == ("not_recorded", None, None)


def test_an_unreadable_manifest_leaves_the_link_unknown(tmp_path):
    """Never "not registered" about a file nobody could open."""
    _task(tmp_path, "alpha", manifest="task:\n  goal: [unclosed\n")

    source = read_workspace_source(tmp_path, project_roots={"other": Path("D:/x")})

    assert source.tasks[0].repo_link.state == "unknown"


def test_a_caller_that_did_not_look_gets_unknown_not_an_absence(tmp_path):
    """`read_workspace_source` without `project_roots` has established nothing about
    projects, and an omitted argument must not turn into a claim."""
    _task(tmp_path, "alpha", manifest=REPO_YAML)

    link = read_workspace_source(tmp_path).tasks[0].repo_link

    assert link.state == "unknown"
    assert link.repo == "D:/Projects/Alpha"


def test_link_repo_treats_an_empty_project_list_as_looked_and_found_none(tmp_path):
    """The distinction the API layer depends on: `{}` is "I listed the projects and
    there are none", `None` is "the listing failed". Only the first may say
    "not registered"."""
    _task(tmp_path, "alpha", manifest=REPO_YAML)
    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert link_repo(manifest, {}).state == "not_registered"
    assert link_repo(manifest, None).state == "unknown"


def test_a_repo_reached_by_a_different_spelling_still_links(tmp_path):
    """`ProjectRegistry.add` stores `root.resolve()`; `task.yaml` holds what the person
    typed. Comparing those lexically calls a registered repository "not registered" the
    moment the file says `.../backend/..` or points through a junction.
    """
    real = tmp_path / "repos" / "alpha"
    real.mkdir(parents=True)
    detour = tmp_path / "repos" / "alpha" / "sub" / ".."
    (real / "sub").mkdir()
    _task(tmp_path, "alpha", manifest=f"task:\n  id: alpha\nprimary_repo: {detour.as_posix()}\n")

    source = read_workspace_source(tmp_path, project_roots={"alpha-repo": real.resolve()})

    assert source.tasks[0].repo_link.state == "linked"


def test_a_path_that_cannot_be_resolved_still_gets_compared(tmp_path):
    """Resolution is a best effort, not a precondition: an offline share must not turn
    a comparison into an exception, and the lexical answer is what this had before."""
    _task(tmp_path, "alpha", manifest="task:\n  id: alpha\nprimary_repo: D:/Offline/Alpha\n")

    def refuse(self, *args, **kwargs):
        raise OSError(53, "The network path was not found")

    import agora.projects.workspace_tasks as module
    original = Path.resolve
    try:
        Path.resolve = refuse  # type: ignore[method-assign]
        source = read_workspace_source(
            tmp_path, project_roots={"alpha-repo": Path("D:/Offline/Alpha")}
        )
    finally:
        Path.resolve = original  # type: ignore[method-assign]
    assert module is not None
    assert source.tasks[0].repo_link.state == "linked"


def test_a_primary_repo_that_is_not_a_path_says_the_value_is_wrong(tmp_path):
    """`primary_repo: 123` records a repository and records it wrongly. Reporting "no
    primary_repo recorded" sends the person to add a line that is already there."""
    _task(tmp_path, "alpha", manifest="task:\n  id: alpha\nprimary_repo: 123\n")

    manifest = read_workspace_source(tmp_path).tasks[0].manifest
    link = read_workspace_source(tmp_path, project_roots={}).tasks[0].repo_link

    assert manifest.primary_repo_malformed is True
    assert manifest.primary_repo is None
    assert link.state == "repo_malformed"


def test_a_blank_primary_repo_is_wrong_rather_than_absent(tmp_path):
    """An empty value is a line someone left unfinished, not a line nobody wrote."""
    _task(tmp_path, "alpha", manifest='task:\n  id: alpha\nprimary_repo: "  "\n')

    assert read_workspace_source(tmp_path, project_roots={}).tasks[0].repo_link.state == (
        "repo_malformed"
    )


def test_a_missing_key_is_still_not_recorded_not_malformed(tmp_path):
    _task(tmp_path, "alpha")  # no primary_repo key at all

    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert manifest.primary_repo_malformed is False
    assert read_workspace_source(tmp_path, project_roots={}).tasks[0].repo_link.state == (
        "not_recorded"
    )


def test_a_project_list_that_could_not_be_read_is_its_own_state(tmp_path):
    """Three causes, three answers. As `{}` every task would read "not registered" —
    a claim about projects nobody saw. As `None` it reads `unknown`, which the panel
    renders as nothing, so the links a person had a moment ago vanish silently.
    """
    _task(tmp_path, "alpha", manifest="task:\n  id: alpha\nprimary_repo: D:/Projects/Alpha\n")

    link = read_workspace_source(tmp_path, project_roots=PROJECTS_UNREADABLE).tasks[0].repo_link

    assert link.state == "projects_unreadable"
    # The path still travels: it is the one thing the panel does know.
    assert link.repo == "D:/Projects/Alpha"


def test_an_unreadable_project_list_never_becomes_not_registered(tmp_path):
    _task(tmp_path, "alpha", manifest="task:\n  id: alpha\nprimary_repo: D:/Projects/Alpha\n")
    manifest = read_workspace_source(tmp_path).tasks[0].manifest

    assert link_repo(manifest, PROJECTS_UNREADABLE).state != "not_registered"
    assert link_repo(manifest, {}).state == "not_registered"


# --- RESUME.md ---------------------------------------------------------------------


def test_the_resume_sections_are_found_under_their_decorated_headings(tmp_path):
    """The headings carry decoration in practice — `## 下一步（按优先级）` — and
    CLAUDE.md fixes only the identifier inside them."""
    _task(tmp_path, "alpha")

    resume = read_workspace_source(tmp_path).tasks[0].resume

    assert resume.available is True
    assert resume.current_state.startswith("停在 W2 的入口。")
    assert resume.next_step == "先打通，再加固。"


def test_a_deeper_heading_belongs_to_the_section_above_it(tmp_path):
    """`### 进度快照` sits under `## 当前状态` in these files, so the state section
    ends at the next `##`, not at the first heading of any rank."""
    _task(tmp_path, "alpha")

    resume = read_workspace_source(tmp_path).tasks[0].resume

    assert "进度快照" in resume.current_state
    assert "先打通" not in resume.current_state


def test_the_progress_snapshot_is_read_by_the_same_verdict_as_the_progress_bar(tmp_path):
    """The default RESUME's 进度快照 is marker-written, so its verdict is `markers`
    with the three counts — and, crucially, it comes from `count_progress_verdict`, the
    reader the dashboard's bar uses, not the marker-only counter it used before."""
    _task(tmp_path, "alpha")

    progress = read_workspace_source(tmp_path).tasks[0].resume.progress

    assert progress is not None
    assert (progress.done, progress.in_progress, progress.todo) == (1, 1, 1)
    assert (progress.convention, progress.reason) == ("markers", None)


def _resume_with_snapshot(body: str) -> str:
    """A RESUME whose 进度快照 section holds exactly `body`. The section sits under
    当前状态 the way these files write it, and 下一步 follows so the section ends."""
    return (
        "# RESUME — alpha\n\n## 当前状态\n\n停在这里。\n\n"
        f"### 进度快照\n\n{body}\n\n## 下一步\n\n继续。\n"
    )


def test_a_checkbox_snapshot_is_counted_with_in_progress_unknown_not_zero(tmp_path):
    """The finding's root cause: a 进度快照 written with `- [x]` boxes used to reach
    `count_progress_markers`, which knows only ✅/🔵/⬜, and came back three zeros —
    read upstream as "tracks nothing yet". The verdict counts it: done + not-done
    partition the whole list, and in_progress is None (the convention has no form for
    work in flight, so 0 would assert something the file never said)."""
    _task(tmp_path, "alpha", resume=_resume_with_snapshot(
        "- [x] W1 完成\n- [X] W1.5 也完成\n- [ ] W2 未完成\n"
    ))

    progress = read_workspace_source(tmp_path).tasks[0].resume.progress

    assert progress is not None
    assert (progress.done, progress.in_progress, progress.todo) == (2, None, 1)
    assert (progress.convention, progress.reason) == ("checkboxes", None)


def test_a_snapshot_mixing_both_conventions_is_uncountable_not_empty(tmp_path):
    """Read fine, but it carries both markers and boxes — neither reading is the file's,
    so the verdict is `mixed_conventions` with no counts. This must reach the UI as
    "read but uncountable", not "empty" and not "RESUME unreadable"."""
    _task(tmp_path, "alpha", resume=_resume_with_snapshot(
        "- ✅ marker item\n- [x] box item, the other convention\n"
    ))

    progress = read_workspace_source(tmp_path).tasks[0].resume.progress

    assert progress is not None
    assert (progress.done, progress.in_progress, progress.todo) == (None, None, None)
    assert (progress.convention, progress.reason) == (None, "mixed_conventions")


def test_a_snapshot_of_marks_this_reader_cannot_count_is_uncountable_not_empty(tmp_path):
    """A checklist in marks with no agreed meaning here (`[/]`, `[-]`). Counting the
    rest would be a floor, so the verdict is `uncounted_markers` — again read fine,
    just uncountable, and distinct from an empty snapshot."""
    _task(tmp_path, "alpha", resume=_resume_with_snapshot(
        "- [x] done\n- [ ] not yet\n- [/] a mark with no meaning here\n"
    ))

    progress = read_workspace_source(tmp_path).tasks[0].resume.progress

    assert progress is not None
    assert (progress.done, progress.in_progress, progress.todo) == (None, None, None)
    assert (progress.convention, progress.reason) == (None, "uncounted_markers")


def test_a_snapshot_section_that_lists_nothing_is_convention_none_not_a_reason(tmp_path):
    """A 进度快照 section that exists and lists no item of either convention: counted,
    three zeros, convention `none`. That is "empty", and it must stay apart from both
    "no section at all" (progress is None) and "uncountable" (a reason)."""
    _task(tmp_path, "alpha", resume=_resume_with_snapshot("还没有开始记录条目。"))

    progress = read_workspace_source(tmp_path).tasks[0].resume.progress

    assert progress is not None
    assert (progress.done, progress.in_progress, progress.todo) == (0, 0, 0)
    assert (progress.convention, progress.reason) == ("none", None)


def test_the_resume_progress_payload_carries_convention_and_reason(tmp_path):
    """The wire shape the frontend reads: counts flattened alongside `convention` and
    `reason`, so `taskProgressOf` can tell a counted checkbox file from an uncountable
    one without a second field. None stays None — no 进度快照 section."""
    _task(tmp_path, "alpha", resume=_resume_with_snapshot(
        "- [x] W1\n- [ ] W2\n"
    ))

    payload = read_workspace_source(tmp_path).tasks[0].resume.payload()["progress"]

    assert payload == {
        "done": 1, "in_progress": None, "todo": 1,
        "convention": "checkboxes", "reason": None,
    }


def test_a_missing_section_is_none_on_a_file_that_was_read(tmp_path):
    """None twice over — no section, and unreadable — is why `reason` has to be
    consulted before the text is rendered."""
    _task(tmp_path, "alpha", resume="# RESUME\n\n## 别的段落\n\n内容\n")

    resume = read_workspace_source(tmp_path).tasks[0].resume

    assert (resume.available, resume.reason) == (True, None)
    assert resume.current_state is None
    assert resume.next_step is None
    # Not {} and not zeroes: no snapshot section was found, so nothing was counted.
    assert resume.progress is None


def test_an_absent_resume_says_absent_and_counts_nothing(tmp_path):
    _task(tmp_path, "alpha", resume=None)

    resume = read_workspace_source(tmp_path).tasks[0].resume

    assert (resume.available, resume.reason) == (False, "absent")
    assert (resume.current_state, resume.next_step, resume.progress) == (None, None, None)


def test_a_resume_past_the_size_bound_is_too_large(tmp_path):
    _task(tmp_path, "alpha", resume="# RESUME\n" + "x" * MAX_RESUME_BYTES)

    assert read_workspace_source(tmp_path).tasks[0].resume.reason == "too_large"


def test_a_resume_that_cannot_be_stat_ed_is_unreadable_not_absent(tmp_path, monkeypatch):
    _task(tmp_path, "alpha")
    _refuse_lstat(monkeypatch, {tmp_path / "tasks" / "alpha" / "RESUME.md"})

    assert read_workspace_source(tmp_path).tasks[0].resume.reason == "unreadable"


def test_a_missing_workflow_is_reported_without_hiding_the_task(tmp_path):
    _task(tmp_path, "alpha", workflow=None)

    task = read_workspace_source(tmp_path).tasks[0]

    assert task.name == "alpha"
    assert task.has_workflow is False


# --- the two readers this one reuses ------------------------------------------------


def test_the_cr_table_inside_resume_is_read_by_the_project_cr_log_parser(tmp_path):
    """Reuse, not a second parser: the table format is identical, so what varies is
    only which file to open and what to call it."""
    _task(tmp_path, "alpha")

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert cr_log.available is True
    assert cr_log.open_count == 1
    assert cr_log.unrecognised_status_count == 0
    assert cr_log.source == "tasks/alpha/RESUME.md"


def test_other_tables_in_a_resume_may_name_change_requests(tmp_path):
    """A RESUME's phase table and acceptance checklist name CRs all the time. Read as
    part of the log, each mention was "a CR id in a row nobody can read" and the card
    said the CR table was malformed, naming rows that were fine."""
    phases = "## 三个阶段\n\n| 阶段 | 内容 |\n|---|---|\n| **P1 读全** | 见 CR-2、CR-44 |\n\n"
    _task(tmp_path, "alpha", resume=RESUME_MD.replace("## 当前状态", phases + "## 当前状态"))

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert (cr_log.available, cr_log.open_count) == (True, 1)
    assert [row.id for row in cr_log.rows] == ["CR-1", "CR-2"]


def test_a_bad_row_inside_the_cr_section_still_makes_the_log_malformed(tmp_path):
    """The scope narrows where the log is, not what counts as broken in it."""
    _task(tmp_path, "alpha", resume=RESUME_MD + "| CR-3 | d | user | 描述 | bug | OPEN | 决定 | a | b |\n")

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert (cr_log.available, cr_log.reason, cr_log.malformed_ids) == (False, "malformed", ("CR-3",))


def test_a_vertical_tab_in_a_cr_cell_does_not_split_the_row(tmp_path):
    """Cutting the section out must not re-break its lines: `splitlines()` treats a
    vertical tab as a line end, and the OPEN row holding one came back with no status."""
    _task(tmp_path, "alpha", resume=RESUME_MD.replace("| agent | 描述 |", "| agent | 描\x0b述 |"))

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert (cr_log.available, cr_log.open_count, cr_log.unrecognised_status_count) == (True, 1, 0)


def test_a_fenced_example_of_the_heading_is_not_the_cr_section(tmp_path):
    """Scoped to an example in a code fence, the real log read as "0 rows, 0 open"."""
    example = "## 写法\n\n```markdown\n## 变更记录 (CR Log)\n\n（示例）\n```\n\n"
    _task(tmp_path, "alpha", resume=RESUME_MD.replace("## 当前状态", example + "## 当前状态"))

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert (cr_log.available, len(cr_log.rows), cr_log.open_count) == (True, 2, 1)


def test_a_log_continued_under_a_second_heading_is_one_log(tmp_path):
    more = ("\n## 别的\n\n| 项 | 说明 |\n|---|---|\n| A1 | 见 CR-9 |\n\n## 变更记录（续）\n\n" + RESUME_HEADER
            + "| CR-3 | 2026-09-07 | user | 描述 | bug | OPEN | 决定 | x |\n")
    _task(tmp_path, "alpha", resume=RESUME_MD + more)

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert ([row.id for row in cr_log.rows], cr_log.open_count) == (["CR-1", "CR-2", "CR-3"], 2)


def test_a_resume_with_no_cr_section_is_still_read_whole(tmp_path):
    """No heading to scope to: a table of CR rows kept under some other heading is
    found, not reported as an empty log."""
    _task(tmp_path, "alpha", resume=RESUME_MD.replace("## 变更记录 (CR Log)", "## Changes"))

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert (cr_log.available, cr_log.open_count) == (True, 1)


def test_an_unreadable_resume_makes_the_cr_log_unreadable_never_zero_open(tmp_path):
    """The load-bearing one: "no CR Log could be read" must not arrive as "0 open"."""
    _task(tmp_path, "alpha", resume=None)

    cr_log = read_workspace_source(tmp_path).tasks[0].cr_log

    assert cr_log.available is False
    assert cr_log.open_count is None


def test_the_task_adr_directory_is_read_by_the_project_decisions_parser(tmp_path):
    directory = _task(tmp_path, "alpha")
    adr = directory / "adr"
    adr.mkdir()
    (adr / "0001-first.md").write_text(
        "# 0001 — First\n\n- Status: accepted\n- Date: 2026-09-06\n\n## Alternatives\n\n无\n",
        encoding="utf-8",
    )

    decisions = read_workspace_source(tmp_path).tasks[0].decisions

    assert decisions.available is True
    assert [r.number for r in decisions.records] == [1]
    assert decisions.source == "tasks/alpha/adr"


def test_a_task_with_no_adr_directory_says_absent_not_no_decisions(tmp_path):
    _task(tmp_path, "alpha")

    decisions = read_workspace_source(tmp_path).tasks[0].decisions

    assert (decisions.available, decisions.reason) == (False, "absent")
    assert decisions.records is None


# --- the acceptance line: live read, not copy ---------------------------------------


def test_reading_a_workspace_writes_nothing_to_it(tmp_path):
    """`task.yaml` W7: "a test proves the write path does not exist, rather than only
    promising it in the documentation".

    Measured against the tree — every path and the digest of its bytes, before and
    after — so it covers a file created, a file changed, and a directory the reader
    might have helpfully made along the way.
    """
    _task(tmp_path, "alpha")
    _task(tmp_path, "beta", manifest="name: another tool\n")
    _task(tmp_path, TEMPLATE_DIR_NAME)
    before = _fingerprint(tmp_path)

    source = read_workspace_source(tmp_path)
    assert source.available is True  # it really did read something

    assert _fingerprint(tmp_path) == before


def test_reading_never_raises_whatever_the_directory_does(tmp_path, monkeypatch):
    """A read is an observation; "the observation failed" is one of its outcomes and
    not an exception the API layer has to remember to catch — a 500 here would take the
    whole panel down over one unreadable folder."""
    _task(tmp_path, "alpha")

    def refuse(self, *args, **kwargs):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(Path, "lstat", refuse)

    source = read_workspace_source(tmp_path)

    assert source.available is False
    assert source.reason == "unreadable"


@pytest.mark.parametrize("error", [
    PermissionError(13, "denied"), OSError(5, "I/O error"), NotADirectoryError(20, "nope"),
])
def test_no_stat_failure_is_ever_read_as_the_workspace_not_being_there(
    tmp_path, monkeypatch, error
):
    _task(tmp_path, "alpha")
    _refuse_lstat(monkeypatch, {tmp_path}, error)

    assert read_workspace_source(tmp_path).reason == "unreadable"
