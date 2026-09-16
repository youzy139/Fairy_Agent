"""fs.py 单元测试：正常路径读写与路径穿越防护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.tools.base import ToolError
from fairy.tools.fs import MAX_READ_BYTES, ListDirTool, ReadFileTool, WriteFileTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """准备一个含文件与子目录的工作区。"""
    (tmp_path / "a.txt").write_text("hello fairy", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("nested", encoding="utf-8")
    return tmp_path


def test_list_dir(workspace: Path) -> None:
    tool = ListDirTool(workspace)
    result = tool.execute(path=".")
    assert "a.txt" in result
    assert "sub" in result
    assert "[目录]" in result and "[文件]" in result


def test_list_dir_subdirectory(workspace: Path) -> None:
    result = ListDirTool(workspace).execute(path="sub")
    assert "b.txt" in result


def test_list_dir_not_found(workspace: Path) -> None:
    with pytest.raises(ToolError, match="目录不存在"):
        ListDirTool(workspace).execute(path="no-such-dir")


def test_read_file(workspace: Path) -> None:
    assert ReadFileTool(workspace).execute(path="a.txt") == "hello fairy"


def test_read_file_nested(workspace: Path) -> None:
    assert ReadFileTool(workspace).execute(path="sub/b.txt") == "nested"


def test_read_file_not_found(workspace: Path) -> None:
    with pytest.raises(ToolError, match="文件不存在"):
        ReadFileTool(workspace).execute(path="missing.txt")


def test_read_file_size_limit(workspace: Path) -> None:
    (workspace / "big.txt").write_bytes(b"x" * (MAX_READ_BYTES + 1))
    with pytest.raises(ToolError, match="文件过大"):
        ReadFileTool(workspace).execute(path="big.txt")


def test_write_file_create(workspace: Path) -> None:
    result = WriteFileTool(workspace).execute(path="new.txt", content="新文件")
    assert "创建" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "新文件"


def test_write_file_overwrite(workspace: Path) -> None:
    result = WriteFileTool(workspace).execute(path="a.txt", content="覆盖内容")
    assert "覆盖" in result
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "覆盖内容"


def test_write_file_creates_parent_dirs(workspace: Path) -> None:
    WriteFileTool(workspace).execute(path="deep/dir/c.txt", content="c")
    assert (workspace / "deep" / "dir" / "c.txt").exists()


@pytest.mark.parametrize(
    "evil_path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "sub/../../escape.txt",
    ],
)
def test_path_traversal_rejected(workspace: Path, evil_path: str) -> None:
    with pytest.raises(ToolError, match="路径越权"):
        ReadFileTool(workspace).execute(path=evil_path)
    with pytest.raises(ToolError, match="路径越权"):
        WriteFileTool(workspace).execute(path=evil_path, content="x")
    with pytest.raises(ToolError, match="路径越权"):
        ListDirTool(workspace).execute(path=evil_path)


def test_absolute_path_outside_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "definitely-outside.txt"
    with pytest.raises(ToolError, match="路径越权"):
        ReadFileTool(workspace).execute(path=str(outside))
    with pytest.raises(ToolError, match="路径越权"):
        WriteFileTool(workspace).execute(path=str(outside), content="x")


def test_symlink_escape_rejected(workspace: Path, tmp_path: Path) -> None:
    """工作区内的符号链接指向外部时，读取必须被拒绝。"""
    outside = tmp_path.parent / "symlink-target.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "escape-link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前平台/权限不支持创建符号链接：{exc}")

    with pytest.raises(ToolError, match="路径越权"):
        ReadFileTool(workspace).execute(path=link.name)


def test_symlink_dir_escape_rejected(workspace: Path, tmp_path: Path) -> None:
    """指向外部目录的符号链接，经其访问文件同样被拒绝。"""
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "escape-dir"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前平台/权限不支持创建符号链接：{exc}")

    with pytest.raises(ToolError, match="路径越权"):
        ReadFileTool(workspace).execute(path=f"{link.name}/secret.txt")
