"""Skills from outside (workspace CR-98): a source repository is fetched, listed, read,
and one skill imported — only as it was shown."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agora.skills import sources as remote
from agora.skills.library import SkillChanged, SkillLibrary, SkillRefused
from agora.terminal.auth import load_or_create_token
from tests.test_skills import md
from tests.test_work import api, world  # noqa: F401  (fixtures)


def _commit(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "-m", "x"], check=True)


@pytest.fixture
def upstream(tmp_path):
    """A published repository of skills, and a source pointing at it."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("skills\n", encoding="utf-8")
    for name, description in (("pdf", "Read and write PDFs"), ("slides", "Make slide decks")):
        d = repo / "skills" / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(md(name, description).replace("---\n\n", "license: Apache-2.0\n---\n\n"),
                                    encoding="utf-8")
        (d / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")
    # A SKILL.md below a skill is that skill's file, not another skill.
    (repo / "skills" / "pdf" / "scripts" / "SKILL.md").write_text("inner\n", encoding="utf-8")
    (repo / "skills" / "broken").mkdir()
    (repo / "skills" / "broken" / "SKILL.md").write_text("no front matter\n", encoding="utf-8")
    _commit(repo)
    return repo, remote.Source("test", "Test", str(repo))


def test_a_source_never_fetched_says_so_rather_than_listing_nothing(tmp_path, upstream):
    _, source = upstream
    got = remote.read(tmp_path / "data", source)
    assert (got.state, got.skills, got.fetched) == ("not_fetched", [], None)


def test_fetch_lists_the_skills_with_what_they_say(tmp_path, upstream):
    _, source = upstream
    got = remote.fetch(tmp_path / "data", source)
    assert got.state == "ok" and got.commit and got.fetched
    assert [(s.name, s.path, s.license) for s in got.skills] == [
        ("broken", "skills/broken", None), ("pdf", "skills/pdf", "Apache-2.0"), ("slides", "skills/slides", "Apache-2.0")]
    assert got.skills[1].description == "Read and write PDFs"
    assert [n["code"] for n in got.skills[0].notes] == ["no_front_matter"]
    # The history is not kept, and reading again needs no network.
    assert not (tmp_path / "data" / remote.CACHE_DIR / "test" / "repo" / ".git").exists()
    assert remote.read(tmp_path / "data", source).payload() == got.payload()


def test_a_failed_fetch_says_why_and_keeps_the_previous_listing(tmp_path, upstream):
    repo, source = upstream
    remote.fetch(tmp_path / "data", source)
    gone = remote.Source("test", "Test", str(tmp_path / "nowhere"))
    with pytest.raises(SkillRefused, match="failed"):
        remote.fetch(tmp_path / "data", gone)
    # ... and a copy fetched from one address is not shown as another's.
    assert remote.read(tmp_path / "data", source).state == "ok"
    assert remote.read(tmp_path / "data", gone).state == "not_fetched"


def test_content_is_the_skill_md_every_file_and_the_hash(tmp_path, upstream):
    _, source = upstream
    remote.fetch(tmp_path / "data", source)
    got = remote.content(tmp_path / "data", source, "skills/pdf")
    assert got["content"].startswith("---\nname: pdf\n") and got["license"] == "Apache-2.0"
    assert [f["path"] for f in got["files"]] == ["SKILL.md", "scripts/SKILL.md", "scripts/run.py"]
    assert len(got["sha"]) == 64
    for not_listed in ("skills", "skills/pdf/scripts", "../upstream/skills/pdf", "skills/pdf/../slides", ""):
        with pytest.raises(SkillRefused, match="not one of the skills"):
            remote.content(tmp_path / "data", source, not_listed)


def test_import_takes_only_what_was_shown(tmp_path, upstream):
    repo, source = upstream
    data, library = tmp_path / "data", SkillLibrary(tmp_path / "lib")
    remote.fetch(data, source)
    shown = remote.content(data, source, "skills/pdf")["sha"]
    for unseen in ("", "0" * 64, remote.content(data, source, "skills/slides")["sha"]):
        with pytest.raises(SkillChanged, match="open it and read it"):
            remote.import_skill(data, source, "skills/pdf", unseen, library, reserved=set())
    assert not (tmp_path / "lib" / "pdf").exists()

    entry = remote.import_skill(data, source, "skills/pdf", shown, library, reserved=set())
    assert (entry["name"], entry["sha"]) == ("pdf", shown)
    assert (tmp_path / "lib" / "pdf" / "scripts" / "run.py").read_text(encoding="utf-8") == "print('x')\n"
    with pytest.raises(SkillRefused, match="already exists"):
        remote.import_skill(data, source, "skills/pdf", shown, library, reserved=set())

    # Upstream changes and is fetched again: the hash shown earlier no longer imports.
    (repo / "skills" / "slides" / "scripts" / "run.py").write_text("print('changed')\n", encoding="utf-8")
    _commit(repo)
    before = remote.content(data, source, "skills/slides")["sha"]
    remote.fetch(data, source)
    with pytest.raises(SkillChanged):
        remote.import_skill(data, source, "skills/slides", before, library, reserved=set())


def test_what_the_library_would_refuse_is_refused_here_too(tmp_path, upstream):
    _, source = upstream
    data, library = tmp_path / "data", SkillLibrary(tmp_path / "lib")
    remote.fetch(data, source)
    sha = remote.content(data, source, "skills/broken")["sha"]
    with pytest.raises(SkillRefused):
        remote.import_skill(data, source, "skills/broken", sha, library, reserved=set())
    sha = remote.content(data, source, "skills/pdf")["sha"]
    with pytest.raises(SkillRefused, match="workflow command"):
        remote.import_skill(data, source, "skills/pdf", sha, library, reserved={"pdf"})


def test_the_preset_source_is_the_official_repository_only():
    assert {s.url for s in remote.SOURCES.values()} == {"https://github.com/anthropics/skills"}
    with pytest.raises(SkillRefused):
        remote.source_of("elsewhere")


def test_the_routes_fetch_list_show_and_import(api, tmp_path, upstream, monkeypatch):  # noqa: F811
    client, _, _ = api
    _, source = upstream
    monkeypatch.setattr(remote, "SOURCES", {"test": source})
    monkeypatch.setenv("AGORA_SKILLS_LIBRARY", str(tmp_path / "lib"))
    for method, path in (("get", "/api/skills/sources"), ("post", "/api/skills/sources/test/fetch"),
                         ("get", "/api/skills/sources/test/skill?path=skills/pdf"),
                         ("post", "/api/skills/sources/test/import")):
        assert getattr(client, method)(path).status_code in (401, 403), path
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")

    assert client.get("/api/skills/sources").json()["sources"][0]["state"] == "not_fetched"
    fetched = client.post("/api/skills/sources/test/fetch")
    assert fetched.status_code == 200 and len(fetched.json()["skills"]) == 3
    shown = client.get("/api/skills/sources/test/skill", params={"path": "skills/pdf"}).json()
    refused = client.post("/api/skills/sources/test/import", json={"path": "skills/pdf", "sha": "0" * 64})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "changed"
    done = client.post("/api/skills/sources/test/import", json={"path": "skills/pdf", "sha": shown["sha"]})
    assert done.status_code == 200 and done.json()["name"] == "pdf"
    assert [s["name"] for s in client.get("/api/skills").json()["library"]["skills"]] == ["pdf"]
    assert client.post("/api/skills/sources/nope/fetch").status_code == 409


def test_a_fetch_cannot_replace_the_copy_while_a_skill_is_being_read(tmp_path, upstream, monkeypatch):
    """The hash handed out must be of the text shown. A fetch landing between reading
    SKILL.md and hashing the tree would pair the old text with the new tree's hash."""
    import threading

    _, source = upstream
    data = tmp_path / "data"
    remote.fetch(data, source)
    real, swapped = remote.tree_sha, threading.Event()

    def hashing(path):
        # A fetch started now has to wait for this read to finish.
        worker = threading.Thread(target=lambda: (remote.fetch(data, source), swapped.set()))
        worker.start()
        worker.join(timeout=1.5)
        assert not swapped.is_set()
        hashing.worker = worker
        return real(path)

    monkeypatch.setattr(remote, "tree_sha", hashing)
    shown = remote.content(data, source, "skills/pdf")
    hashing.worker.join(timeout=60)
    monkeypatch.setattr(remote, "tree_sha", real)
    assert swapped.is_set() and shown["sha"] == real(data / remote.CACHE_DIR / "test" / "repo" / "skills" / "pdf")
