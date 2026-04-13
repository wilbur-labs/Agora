"""Tests for tools — file_ops, shell, registry."""
import os
import tempfile

import pytest

from agora.tools.base import ToolResult
from agora.tools.file_ops import ReadFile, WriteFile, PatchFile, ListDir
from agora.tools.shell import Shell
from agora.tools.registry import ToolRegistry


# ── ReadFile ──

@pytest.mark.asyncio
async def test_read_file_success():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello world")
        path = f.name
    try:
        r = await ReadFile().execute(path=path)
        assert r.success is True
        assert r.output == "hello world"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_read_file_not_found():
    r = await ReadFile().execute(path="/nonexistent_file_xyz_123")
    assert r.success is False
    assert "not found" in r.error.lower()


@pytest.mark.asyncio
async def test_read_file_truncation():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("x" * 200_000)
        path = f.name
    try:
        r = await ReadFile().execute(path=path)
        assert r.success is True
        assert "truncated" in r.output
        assert len(r.output) < 200_000
    finally:
        os.unlink(path)


# ── WriteFile ──

@pytest.mark.asyncio
async def test_write_file_creates():
    path = tempfile.mktemp(suffix=".txt")
    try:
        r = await WriteFile().execute(path=path, content="test content")
        assert r.success is True
        assert os.path.exists(path)
        assert open(path).read() == "test content"
    finally:
        if os.path.exists(path):
            os.unlink(path)


@pytest.mark.asyncio
async def test_write_file_creates_dirs():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "sub", "deep", "file.txt")
    try:
        r = await WriteFile().execute(path=path, content="nested")
        assert r.success is True
        assert open(path).read() == "nested"
    finally:
        import shutil
        shutil.rmtree(d)


# ── PatchFile ──

@pytest.mark.asyncio
async def test_patch_file_success():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello world")
        path = f.name
    try:
        r = await PatchFile().execute(path=path, old_str="world", new_str="agora")
        assert r.success is True
        assert open(path).read() == "hello agora"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_patch_file_not_found():
    r = await PatchFile().execute(path="/nonexistent", old_str="a", new_str="b")
    assert r.success is False


@pytest.mark.asyncio
async def test_patch_file_no_match():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello")
        path = f.name
    try:
        r = await PatchFile().execute(path=path, old_str="xyz", new_str="abc")
        assert r.success is False
        assert "not found" in r.error.lower()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_patch_file_multiple_matches():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("aaa aaa")
        path = f.name
    try:
        r = await PatchFile().execute(path=path, old_str="aaa", new_str="bbb")
        assert r.success is False
        assert "2" in r.error  # matches 2 times
    finally:
        os.unlink(path)


# ── ListDir ──

@pytest.mark.asyncio
async def test_list_dir_success():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "a.txt"), "w").close()
    os.mkdir(os.path.join(d, "subdir"))
    try:
        r = await ListDir().execute(path=d)
        assert r.success is True
        assert "subdir" in r.output
        assert "a.txt" in r.output
    finally:
        import shutil
        shutil.rmtree(d)


@pytest.mark.asyncio
async def test_list_dir_not_a_dir():
    r = await ListDir().execute(path="/nonexistent_dir_xyz")
    assert r.success is False


# ── Shell ──

@pytest.mark.asyncio
async def test_shell_success():
    r = await Shell().execute(command="echo hello")
    assert r.success is True
    assert "hello" in r.output


@pytest.mark.asyncio
async def test_shell_failure():
    r = await Shell().execute(command="ls /nonexistent_path_xyz_123")
    assert r.success is False


@pytest.mark.asyncio
async def test_shell_cwd():
    r = await Shell().execute(command="pwd", cwd="/tmp")
    assert r.success is True
    assert "/tmp" in r.output


@pytest.mark.asyncio
async def test_shell_timeout():
    shell = Shell()
    # Monkey-patch timeout for test
    import agora.tools.shell as mod
    old = mod._TIMEOUT
    mod._TIMEOUT = 1
    try:
        r = await shell.execute(command="sleep 10")
        assert r.success is False
        assert "timed out" in r.error.lower()
    finally:
        mod._TIMEOUT = old


# ── ToolRegistry ──

def test_registry_has_all_tools():
    reg = ToolRegistry()
    names = [t.name for t in reg.all()]
    assert "read_file" in names
    assert "write_file" in names
    assert "patch_file" in names
    assert "list_dir" in names
    assert "shell" in names


def test_registry_get():
    reg = ToolRegistry()
    assert reg.get("read_file") is not None
    assert reg.get("nonexistent") is None


def test_registry_function_schemas():
    reg = ToolRegistry()
    schemas = reg.function_schemas()
    assert len(schemas) == 5
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]
