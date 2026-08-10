from __future__ import annotations

import pytest

from agora.execution.deliverables import list_changes, read_file


def test_read_file_returns_confined_bounded_content(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("hello world", encoding="utf-8")
    result = read_file(tmp_path, "docs/note.md")
    assert result["path"] == "docs/note.md"
    assert result["content"] == "hello world"
    assert result["truncated"] is False


def test_read_file_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises((PermissionError, FileNotFoundError)):
        read_file(tmp_path, "../outside-secret.txt")


def test_read_file_missing_is_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path, "does-not-exist.md")


def test_list_changes_reports_untracked_deliverable(tmp_path):
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    subprocess.run([git, "-C", str(tmp_path), "init", "-q"], check=True, timeout=10)
    (tmp_path / "deliverable.txt").write_text("x", encoding="utf-8")
    files = list_changes(tmp_path)
    assert any(f["path"] == "deliverable.txt" and f["status"] == "untracked" for f in files)
