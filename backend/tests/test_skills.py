"""The skills library and installing into projects (workspace CR-75 slice 5)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agora.skills import install as installer
from agora.skills.inventory import importable, library_root, reserved_names, skill_projects, workflow_commands
from agora.skills.library import MARKER, SkillChanged, SkillLibrary, SkillRefused, check_skill_md
from agora.terminal.auth import load_or_create_token
from agora.work.projects import work_key
from tests.test_work import FakeSession, _service, _task, _tool, api, run, world  # noqa: F401  (fixtures)


def md(name: str, description: str = "Does a thing", body: str = "Body.\n") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def exclude_of(repo: Path) -> str:
    return (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")


# -- the library -------------------------------------------------------------------

def test_an_absent_library_is_empty_not_an_error(tmp_path):
    read = SkillLibrary(tmp_path / "skills").read()
    assert (read.state, read.skills) == ("absent", [])


def test_an_unknown_library_says_why():
    read = SkillLibrary(None, "two work roots").read()
    assert (read.state, read.reason) == ("unknown", "two work roots")
    with pytest.raises(SkillRefused, match="two work roots"):
        SkillLibrary(None, "two work roots").create("x", md("x"), reserved=set())


def test_create_read_update_delete(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    entry = lib.create("pdf-tips", md("pdf-tips"), reserved=set())
    assert entry.description == "Does a thing" and entry.sha and entry.error is None
    content = lib.content("pdf-tips")
    lib.update("pdf-tips", md("pdf-tips", "Better"), content["content_sha"])
    assert lib.entry("pdf-tips").description == "Better"
    with pytest.raises(SkillChanged):
        lib.update("pdf-tips", md("pdf-tips", "Stale"), content["content_sha"])
    with pytest.raises(SkillRefused, match="already exists"):
        lib.create("pdf-tips", md("pdf-tips"), reserved=set())
    lib.delete("pdf-tips", [])
    assert lib.read().skills == []


@pytest.mark.parametrize("name,text,match", [
    ("Bad_Name", md("Bad_Name"), "lower-case"),
    ("ok", "no front matter", "front matter"),
    ("ok", md("other"), "name must be"),
    ("ok", "---\nname: ok\n---\n", "description"),
])
def test_what_every_cli_could_not_load_is_refused(tmp_path, name, text, match):
    with pytest.raises(SkillRefused, match=match):
        SkillLibrary(tmp_path / "skills").create(name, text, reserved=set())


def test_a_workflow_command_name_is_refused(tmp_path):
    # In claude a skill replaces the command of the same name.
    with pytest.raises(SkillRefused, match="workflow command"):
        SkillLibrary(tmp_path / "skills").create("adr", md("adr"), reserved={"adr"})


def test_delete_refuses_while_installed(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.create("x", md("x"), reserved=set())
    with pytest.raises(SkillRefused, match="still installed in alpha"):
        lib.delete("x", ["alpha"])


def test_an_unreadable_skill_is_listed_with_its_reason(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    (tmp_path / "skills" / "nomd").mkdir(parents=True)
    [entry] = lib.read().skills
    assert (entry.name, entry.error) == ("nomd", "no SKILL.md")


def test_a_crlf_skill_md_can_be_edited(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    (tmp_path / "skills" / "x").mkdir(parents=True)
    (tmp_path / "skills" / "x" / "SKILL.md").write_bytes(md("x").replace("\n", "\r\n").encode("utf-8"))
    content = lib.content("x")
    assert "\r\n" in content["content"]
    lib.update("x", md("x", "saved"), content["content_sha"])
    assert lib.entry("x").description == "saved"


def test_a_directory_that_cannot_be_listed_refuses_rather_than_shrinks(tmp_path, monkeypatch):
    import os

    from agora.skills.library import tree_files

    skill = tmp_path / "x"
    (skill / "sub").mkdir(parents=True)
    real = os.scandir

    def scandir(path):
        if Path(path).name == "sub":
            raise PermissionError("denied")
        return real(path)
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(PermissionError):
        tree_files(skill)


def test_a_copy_keeps_the_file_mode(tmp_path):
    import os
    import stat

    from agora.skills.library import copy_tree

    (tmp_path / "src").mkdir()
    script = tmp_path / "src" / "run.sh"
    script.write_text("echo\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    copy_tree(tmp_path / "src", tmp_path / "dst")
    assert os.stat(tmp_path / "dst" / "run.sh").st_mode == script.stat().st_mode


def test_the_standards_musts_are_refused_on_write(tmp_path):
    """agentskills.io: description at most 1024 characters, no consecutive hyphens."""
    lib = SkillLibrary(tmp_path / "lib")
    with pytest.raises(SkillRefused, match="1024"):
        lib.create("long", md("long", description="d" * 1025), reserved=set())
    lib.create("edge", md("edge", description="d" * 1024), reserved=set())
    with pytest.raises(SkillRefused, match="single hyphens"):
        lib.create("a--b", md("a--b"), reserved=set())
    # One named before the rule is still read, flagged, and removable.
    (tmp_path / "lib" / "a--b").mkdir()
    (tmp_path / "lib" / "a--b" / "SKILL.md").write_text(md("a--b"), encoding="utf-8")
    assert lib.content("a--b")["notes"] == [{"code": "bad_name"}]
    lib.delete("a--b", [])
    assert not (tmp_path / "lib" / "a--b").exists()


def test_a_skill_written_elsewhere_is_read_with_what_departs_from_the_standard(tmp_path):
    from agora.skills.library import read_entry, standard_notes

    assert standard_notes("ok", md("ok")) == []
    assert standard_notes("ok", "no front matter") == [{"code": "no_front_matter"}]
    text = ("---\nname: other\ndescription: " + "d" * 1030 + "\nversion: 2\nmodel: x\ncompatibility: 7\n---\n"
            + "line\n" * 500)
    codes = {n["code"]: n for n in standard_notes("ok", text)}
    assert set(codes) == {"name_mismatch", "description_too_long", "bad_compatibility", "extra_fields", "long_body"}
    assert codes["extra_fields"]["fields"] == "model, version"
    assert codes["description_too_long"]["length"] == 1030 and codes["long_body"]["lines"] == 507

    # Reading keeps working: the entry is listed with its notes, not turned into an error.
    place = tmp_path / "place" / "theirs"
    place.mkdir(parents=True)
    (place / "SKILL.md").write_text("---\nname: theirs\ndescription: d\neffort: high\n---\nbody\n", encoding="utf-8")
    entry = read_entry(place)
    assert entry.error is None and entry.notes == [{"code": "extra_fields", "fields": "effort"}]
    found = [f for f in importable([(tmp_path / "place", "here")]) if f.where == "here"]
    assert found[0].payload()["notes"] == entry.notes


def test_check_skill_md_returns_the_description():
    assert check_skill_md("a", md("a", "  hi  ")) == "hi"


# -- installing ------------------------------------------------------------------------

@pytest.fixture
def setup(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.create("x", md("x"), reserved=set())
    (tmp_path / "skills" / "x" / "scripts").mkdir()
    (tmp_path / "skills" / "x" / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    return lib, git_repo(tmp_path / "repo")


def test_install_copies_to_all_three_and_keeps_them_out_of_git(setup):
    lib, repo = setup
    result = installer.install(lib, repo, "x")
    assert {c["state"] for c in result["states"]} == {"current"} and result["excluded"] is True
    for target in installer.TARGETS.values():
        copy = repo / target / "x"
        assert (copy / "SKILL.md").is_file() and (copy / "scripts" / "run.py").is_file()
        assert json.loads((copy / MARKER).read_text(encoding="utf-8"))["skill"] == "x"
        assert f"/{target}/x/" in exclude_of(repo)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    assert status == ""
    # Installing again changes nothing and adds no duplicate line.
    installer.install(lib, repo, "x")
    assert exclude_of(repo).count("/.claude/skills/x/") == 1


def test_a_library_edit_shows_as_outdated_and_reinstall_updates(setup):
    lib, repo = setup
    installer.install(lib, repo, "x")
    lib.update("x", md("x", "v2"), lib.content("x")["content_sha"])
    sha = lib.entry("x").sha
    assert installer.summary(installer.skill_states(repo, "x", sha)) == "outdated"
    installer.install(lib, repo, "x")
    assert installer.summary(installer.skill_states(repo, "x", sha)) == "installed"
    assert "v2" in (repo / ".agents/skills/x/SKILL.md").read_text(encoding="utf-8")


def test_a_copy_edited_in_place_is_not_overwritten_or_deleted_without_force(setup):
    lib, repo = setup
    installer.install(lib, repo, "x")
    (repo / ".claude/skills/x/SKILL.md").write_text(md("x", "mine"), encoding="utf-8")
    assert installer.summary(installer.skill_states(repo, "x", lib.entry("x").sha)) == "modified"
    with pytest.raises(SkillRefused, match="edited in place"):
        installer.install(lib, repo, "x")
    with pytest.raises(SkillRefused, match="edited in place"):
        installer.uninstall(repo, "x")
    assert "mine" in (repo / ".claude/skills/x/SKILL.md").read_text(encoding="utf-8")
    installer.install(lib, repo, "x", force=True)
    assert installer.summary(installer.skill_states(repo, "x", lib.entry("x").sha)) == "installed"


def test_a_directory_agora_did_not_install_is_never_touched(setup):
    lib, repo = setup
    foreign = repo / ".kiro/skills/x"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("theirs", encoding="utf-8")
    with pytest.raises(SkillRefused, match="foreign"):
        installer.install(lib, repo, "x", force=True)
    # Nothing was written anywhere: the check runs before the first copy.
    assert not (repo / ".claude/skills/x").exists()
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "theirs"


def test_uninstall_removes_the_copies_and_keeps_the_exclude_lines(setup):
    # Linked worktrees share one exclude file: another one may still hold a copy.
    lib, repo = setup
    installer.install(lib, repo, "x")
    result = installer.uninstall(repo, "x")
    assert result["left_foreign"] == []
    for target in installer.TARGETS.values():
        assert not (repo / target / "x").exists()
        assert f"/{target}/x/" in exclude_of(repo)


def test_a_project_below_the_repository_top_gets_anchored_lines(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.create("x", md("x"), reserved=set())
    top = git_repo(tmp_path / "mono")
    sub = top / "apps" / "web"
    sub.mkdir(parents=True)
    installer.install(lib, sub, "x")
    assert "/apps/web/.claude/skills/x/" in exclude_of(top)
    status = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    assert status == ""


def test_outside_git_the_copies_are_made_and_the_result_says_no_exclude(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.create("x", md("x"), reserved=set())
    plain = tmp_path / "plain"
    plain.mkdir()
    assert installer.install(lib, plain, "x")["excluded"] is None


# -- inventory -----------------------------------------------------------------------

def test_the_library_sits_in_the_one_claude_work_root(tmp_path, monkeypatch):
    monkeypatch.delenv("AGORA_SKILLS_LIBRARY", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    assert library_root([work]) == (work / "skills", None)
    other = tmp_path / "other"
    other.mkdir()
    root, reason = library_root([work, other])
    assert root is None and "2 claude-workspace" in reason
    monkeypatch.setenv("AGORA_SKILLS_LIBRARY", str(tmp_path / "lib"))
    assert library_root([work, other]) == (tmp_path / "lib", None)


def test_workflow_commands_are_listed_and_reserved(world):
    tmp_path, work, _ = world
    commands = tmp_path / "tool" / ".claude" / "commands"
    commands.mkdir()
    (commands / "adr.md").write_text("---\ndescription: Start an ADR\n---\nbody", encoding="utf-8")
    found = workflow_commands([work])
    assert [(f.name, f.description, f.kind) for f in found] == [("adr", "Start an ADR", "workflow")]
    assert reserved_names(found) == {"adr"}


def test_a_kiro_workspaces_skills_are_listed_in_either_layout(tmp_path):
    # workspace CR-92 moved kiro-workspace's skills to <name>/SKILL.md; reading only the
    # flat layout made every one of them disappear from the list without a word.
    kiro = tmp_path / "kiro-workspace"
    skills = kiro / ".kiro" / "skills"
    (skills / "adr").mkdir(parents=True)
    (skills / "adr" / "SKILL.md").write_text(md("adr", "Record a decision"), encoding="utf-8")
    (skills / "old-style.md").write_text("# Old style\n\nBody.\n", encoding="utf-8")
    (skills / "README.md").write_text("# Convention\n", encoding="utf-8")
    found = workflow_commands([kiro])
    assert sorted((f.name, f.description, f.where) for f in found) == [
        ("adr", "Record a decision", "kiro-workspace"),
        ("old-style", "Old style", "kiro-workspace"),
    ]
    assert reserved_names(found) == set()


def test_projects_carry_their_primary_repo_or_why_not(world, tmp_path):
    _, work, _ = world
    repo = git_repo(tmp_path / "repo")
    _task(work, "alpha", f"task:\n  title: Alpha\nprimary_repo: {repo.as_posix()}\n")
    by_task = {p.task: p for p in skill_projects([work])}
    assert by_task["alpha"].repo == str(repo)
    assert by_task["beta"].repo is None and "no primary_repo" in by_task["beta"].reason
    assert by_task["alpha"].shelved is False


def test_a_bracket_in_the_project_path_is_excluded_literally(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.create("x", md("x"), reserved=set())
    top = git_repo(tmp_path / "mono")
    sub = top / "apps" / "[web]"
    sub.mkdir(parents=True)
    installer.install(lib, sub, "x")
    status = subprocess.run(["git", "-C", str(top), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    assert status == ""


def test_delete_refuses_while_a_project_cannot_be_read(api, tmp_path, monkeypatch):
    client, _, work = api
    monkeypatch.setenv("AGORA_SKILLS_LIBRARY", str(tmp_path / "lib"))
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    assert client.post("/api/skills/library", json={"name": "x", "content": md("x")}).status_code == 200
    # The world fixture already holds a task whose task.yaml is broken.
    refused = client.delete("/api/skills/library/x")
    assert refused.status_code == 409 and "cannot tell" in refused.json()["detail"]["message"]


def test_paused_and_archived_projects_still_count(world, tmp_path):
    _, work, _ = world
    repo = git_repo(tmp_path / "repo")
    _task(work, "alpha", f"task:\n  title: Alpha\n  status: paused\nprimary_repo: {repo.as_posix()}\n")
    archived = work / "tasks" / "_archive" / "gone"
    archived.mkdir(parents=True)
    (archived / "task.yaml").write_text(f"task:\n  title: Gone\nprimary_repo: {repo.as_posix()}\n", encoding="utf-8")
    by_task = {p.task: p for p in skill_projects([work])}
    assert by_task["alpha"].shelved and by_task["alpha"].repo == str(repo)
    assert by_task["gone"].shelved and by_task["gone"].repo == str(repo)


def test_importable_skips_copies_agora_installed(tmp_path):
    place = tmp_path / "place"
    (place / "theirs").mkdir(parents=True)
    (place / "theirs" / "SKILL.md").write_text(md("theirs"), encoding="utf-8")
    (place / "ours").mkdir()
    (place / "ours" / "SKILL.md").write_text(md("ours"), encoding="utf-8")
    (place / "ours" / MARKER).write_text("{}", encoding="utf-8")
    names = [f.name for f in importable([(place, "here")]) if f.where == "here"]
    assert names == ["theirs"]


# -- the routes ----------------------------------------------------------------------

def test_the_skills_routes_need_the_terminal_token(api):
    client, _, _ = api
    assert client.get("/api/skills").status_code == 401


def test_create_install_and_import_through_the_api(api, tmp_path, monkeypatch):
    client, _, work = api
    monkeypatch.setenv("AGORA_SKILLS_LIBRARY", str(tmp_path / "lib"))
    repo = git_repo(tmp_path / "repo")
    _task(work, "alpha", f"task:\n  title: Alpha\nprimary_repo: {repo.as_posix()}\n")
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")

    assert client.post("/api/skills/library", json={"name": "x", "content": md("x")}).status_code == 200
    bad = client.post("/api/skills/library", json={"name": "y", "content": "nope"})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "refused"
    alpha = work_key(work, "alpha")
    assert client.post("/api/skills/library/x/install", json={"project": alpha}).status_code == 200
    view = client.get("/api/skills").json()
    assert view["installs"]["x"][alpha]["summary"] == "installed"
    beta = work_key(work, "beta")
    assert client.post("/api/skills/library/x/install", json={"project": beta}).status_code == 409
    assert client.post("/api/skills/library/x/install", json={"project": "nope"}).status_code == 404
    refused = client.delete("/api/skills/library/x")
    assert refused.status_code == 409 and "alpha" in refused.json()["detail"]["message"]

    # Import: a foreign skill in the repo is offered; an arbitrary path is not.
    (repo / ".claude/skills/theirs").mkdir(parents=True)
    (repo / ".claude/skills/theirs/SKILL.md").write_text(md("theirs"), encoding="utf-8")
    offered = [f for f in client.get("/api/skills").json()["importable"] if f["name"] == "theirs"]
    assert len(offered) == 1
    assert client.post("/api/skills/import", json={"path": str(tmp_path), "name": "theirs"}).status_code == 409
    assert client.post("/api/skills/import", json={"path": offered[0]["path"], "name": "theirs"}).status_code == 200
    assert (repo / ".claude/skills/theirs/SKILL.md").is_file()  # the original stays

    assert client.post("/api/skills/library/x/uninstall", json={"project": alpha}).status_code == 200
    for broken in ("broken", "listy"):  # "empty" has no task.yaml: never a project, no gap
        import shutil
        shutil.rmtree(work / "tasks" / broken)
    assert client.delete("/api/skills/library/x").status_code == 200


def test_a_listed_skill_can_be_read_and_nothing_else(api, tmp_path, monkeypatch):
    """The page shows what an importable skill or a workflow command says (read-only);
    the route reads only the paths the view lists."""
    client, _, work = api
    monkeypatch.setenv("AGORA_SKILLS_LIBRARY", str(tmp_path / "lib"))
    repo = git_repo(tmp_path / "repo")
    _task(work, "alpha", f"task:\n  title: Alpha\nprimary_repo: {repo.as_posix()}\n")
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    theirs = repo / ".claude/skills/theirs"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text(md("theirs", body="Their body.\n"), encoding="utf-8")
    (theirs / "helper.py").write_text("pass\n", encoding="utf-8")
    commands = tmp_path / "tool" / ".claude" / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    (commands / "adr.md").write_text("---\ndescription: Start an ADR\n---\nADR body", encoding="utf-8")

    view = client.get("/api/skills").json()
    offered = [f for f in view["importable"] if f["name"] == "theirs"][0]
    got = client.get("/api/skills/found", params={"path": offered["path"]})
    assert got.status_code == 200
    assert "Their body." in got.json()["content"] and got.json()["kind"] == "importable"
    assert sorted(got.json()["files"]) == ["SKILL.md", "helper.py"]

    command = [f for f in view["workflow"] if f["name"] == "adr"][0]
    got = client.get("/api/skills/found", params={"path": command["path"]})
    assert got.status_code == 200 and "ADR body" in got.json()["content"] and got.json()["kind"] == "workflow"

    # Not listed: a file that exists, the directory above a listed one, a file inside one.
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    for path in (str(secret), str(theirs.parent), str(theirs / "SKILL.md")):
        refused = client.get("/api/skills/found", params={"path": path})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "refused"
    client.headers.pop("X-Agora-Terminal-Token")
    assert client.get("/api/skills/found", params={"path": offered["path"]}).status_code == 401


def test_the_work_page_adds_the_task_repository_for_its_skills(world, tmp_path):
    from agora.work.service import _skills_repo
    from agora.work.projects import scan_work_projects

    _, work, _ = world
    repo = git_repo(tmp_path / "repo")
    _task(work, "alpha", f"task:\n  title: Alpha\nprimary_repo: {repo.as_posix()}\n")
    projects = {p.task: p for p in scan_work_projects([work]).projects}
    assert _skills_repo(projects["alpha"]) == str(repo)
    assert _skills_repo(projects["beta"]) is None
