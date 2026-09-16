"""search.py 单元测试：文件名 glob 搜索、内容关键词过滤与工作区边界。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.tools.base import ToolError
from fairy.tools.search import SearchFilesTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """准备一个含多层文件的工作区。"""
    (tmp_path / "a.py").write_text("print('hello fairy')", encoding="utf-8")
    (tmp_path / "b.md").write_text("# 说明", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("import os", encoding="utf-8")
    (tmp_path / "sub" / "d.txt").write_text("fairy 关键词在此", encoding="utf-8")
    return tmp_path


def test_search_by_glob(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="*.py")
    assert "a.py" in result
    # glob 的 * 不递归子目录，递归匹配见 test_search_recursive_glob
    assert "c.py" not in result
    assert "b.md" not in result


def test_search_recursive_glob(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="**/*.py")
    assert "a.py" in result and "c.py" in result


def test_search_in_subdirectory(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="*.txt", path="sub")
    assert "d.txt" in result
    assert "a.py" not in result


def test_search_with_keyword(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="**/*.txt", keyword="关键词")
    assert "d.txt" in result


def test_search_keyword_no_match(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="**/*.py", keyword="不存在的内容")
    assert "未找到" in result


def test_search_no_match(workspace: Path) -> None:
    result = SearchFilesTool(workspace).execute(pattern="*.java")
    assert "未找到" in result


def test_search_empty_pattern_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError, match="不能为空"):
        SearchFilesTool(workspace).execute(pattern="  ")


def test_search_start_dir_not_found(workspace: Path) -> None:
    with pytest.raises(ToolError, match="目录不存在"):
        SearchFilesTool(workspace).execute(pattern="*.py", path="no-such-dir")


def test_search_path_traversal_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError, match="路径越权"):
        SearchFilesTool(workspace).execute(pattern="*.py", path="../../..")


def test_search_symlink_escape_skipped(workspace: Path, tmp_path: Path) -> None:
    """指向工作区外的符号链接文件不出现在搜索结果中。"""
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "escape.py"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前平台/权限不支持创建符号链接：{exc}")

    result = SearchFilesTool(workspace).execute(pattern="*.py")
    assert "escape.py" not in result
    assert "a.py" in result
