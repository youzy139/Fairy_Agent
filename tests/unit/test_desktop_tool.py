"""desktop.py 单元测试：分类、dry_run 不移动、执行移动、防覆盖、跳过规则。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.tools.base import ToolError
from fairy.tools.desktop import OrganizeDesktopTool, find_desktop


@pytest.fixture
def desktop(tmp_path: Path) -> Path:
    """造一个含各类文件的假桌面。"""
    d = tmp_path / "Desktop"
    d.mkdir()
    (d / "照片.jpg").write_text("img", encoding="utf-8")
    (d / "报告.docx").write_text("doc", encoding="utf-8")
    (d / "笔记.txt").write_text("note", encoding="utf-8")
    (d / "游戏.lnk").write_text("lnk", encoding="utf-8")
    (d / "setup.exe").write_text("exe", encoding="utf-8")
    (d / "神秘文件.xyz").write_text("???", encoding="utf-8")
    (d / "desktop.ini").write_text("sys", encoding="utf-8")
    (d / ".hidden").write_text("hide", encoding="utf-8")
    (d / "已有文件夹").mkdir()
    return d


def test_dry_run_does_not_move(desktop: Path) -> None:
    result = OrganizeDesktopTool(desktop).execute(dry_run=True)
    assert "预览模式" in result
    assert "照片.jpg" in result
    # 文件还在原地
    assert (desktop / "照片.jpg").exists()
    assert not (desktop / "图片").exists()


def test_execute_moves_files(desktop: Path) -> None:
    result = OrganizeDesktopTool(desktop).execute(dry_run=False)
    assert "已完成整理" in result
    assert (desktop / "图片" / "照片.jpg").exists()
    assert (desktop / "文档" / "报告.docx").exists()
    assert (desktop / "文档" / "笔记.txt").exists()
    assert (desktop / "快捷方式" / "游戏.lnk").exists()
    assert (desktop / "安装程序" / "setup.exe").exists()
    assert (desktop / "其他" / "神秘文件.xyz").exists()


def test_skip_rules(desktop: Path) -> None:
    OrganizeDesktopTool(desktop).execute(dry_run=False)
    # 系统文件、隐藏文件、目录保持不动
    assert (desktop / "desktop.ini").exists()
    assert (desktop / ".hidden").exists()
    assert (desktop / "已有文件夹").is_dir()


def test_name_collision_appends_suffix(desktop: Path) -> None:
    (desktop / "图片").mkdir()
    (desktop / "图片" / "照片.jpg").write_text("existing", encoding="utf-8")
    OrganizeDesktopTool(desktop).execute(dry_run=False)
    assert (desktop / "图片" / "照片 (1).jpg").exists()
    # 原文件未被覆盖
    assert (desktop / "图片" / "照片.jpg").read_text(encoding="utf-8") == "existing"


def test_empty_desktop(desktop: Path) -> None:
    for f in desktop.iterdir():
        if f.is_file():
            f.unlink()
    result = OrganizeDesktopTool(desktop).execute()
    assert "没有需要整理" in result


def test_missing_desktop_rejected(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="不存在"):
        OrganizeDesktopTool(tmp_path / "no-desktop").execute()


def test_find_desktop_returns_path() -> None:
    # 真实机器上应能找到桌面；找不到也是可接受的错误信息
    try:
        path = find_desktop()
        assert path.is_dir()
    except ToolError as exc:
        assert "未找到桌面目录" in str(exc)
