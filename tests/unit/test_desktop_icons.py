"""desktop_icons.py 单元测试：注入内存桩 backend，不触真实桌面。

真实 win32 路径（_desktop_listview）依赖 explorer.exe 桌面进程，
无法在 CI/无头环境验证，这里只测工具层的匹配、布局与错误转换逻辑。
"""

from __future__ import annotations

import pytest

from fairy.tools.base import ToolError
from fairy.tools.desktop_icons import ArrangeDesktopTool, ListDesktopIconsTool

# 布局参数与 desktop_icons.py 保持一致
START_X, START_Y, COL_W, ROW_H = 40, 40, 110, 100


class FakeBackend:
    """内存桩：模拟桌面 ListView（名称 + 坐标 + 自动排列开关）。"""

    def __init__(self, names: list[str], auto_arrange: bool = False) -> None:
        self.icons = [
            {"index": i, "name": name, "x": 900 + i * 10, "y": 900 + i * 10}
            for i, name in enumerate(names)
        ]
        self.auto_arrange = auto_arrange
        self.moves: list[tuple[int, int, int]] = []

    def find_desktop_listview(self) -> int:
        return 1234

    def list_icons(self, hwnd: int) -> list[dict]:
        assert hwnd == 1234
        return [dict(icon) for icon in self.icons]

    def set_icon_position(self, hwnd: int, index: int, x: int, y: int) -> None:
        self.moves.append((index, x, y))
        self.icons[index]["x"] = x
        self.icons[index]["y"] = y

    def is_auto_arrange(self, hwnd: int) -> bool:
        return self.auto_arrange

    def set_auto_arrange(self, hwnd: int, enabled: bool) -> None:
        self.auto_arrange = enabled

    def pos_of(self, name: str) -> tuple[int, int]:
        icon = next(i for i in self.icons if i["name"] == name)
        return icon["x"], icon["y"]


GAMES = ["原神", "崩坏：星穹铁道", "绝区零", "回收站", "QQ"]
MIHOYO = [{"label": "米哈游", "icons": ["绝区零", "崩铁", "原神"]}]


def test_list_returns_names_coords_and_auto_arrange() -> None:
    backend = FakeBackend(["绝区零", "回收站"], auto_arrange=True)
    result = ListDesktopIconsTool(backend=backend).execute()
    assert "绝区零" in result and "(900, 900)" in result
    assert "回收站" in result and "(910, 910)" in result
    assert "自动排列：开启" in result
    assert ListDesktopIconsTool(backend=backend).permission == "read"


def test_arrange_dry_run_only_previews() -> None:
    backend = FakeBackend(GAMES)
    result = ArrangeDesktopTool(backend=backend).execute(groups=MIHOYO, dry_run=True)
    assert "预览模式" in result
    assert "[米哈游]" in result
    assert "崩坏：星穹铁道" in result
    # 方案里的目标坐标：米哈游列 x=40，自上而下 y=40/140/240
    assert f"绝区零 → ({START_X}, {START_Y})" in result
    assert f"崩坏：星穹铁道 → ({START_X}, {START_Y + ROW_H})" in result
    assert f"原神 → ({START_X}, {START_Y + 2 * ROW_H})" in result
    # 不动任何图标
    assert backend.moves == []
    assert backend.pos_of("绝区零") == (920, 920)


def test_arrange_executes_layout_and_disables_auto_arrange() -> None:
    backend = FakeBackend(GAMES, auto_arrange=True)
    tool = ArrangeDesktopTool(backend=backend)
    assert tool.permission == "dangerous"
    result = tool.execute(groups=MIHOYO, dry_run=False)

    # 自动排列被关闭并记录
    assert backend.auto_arrange is False
    assert "已关闭桌面「自动排列图标」" in result
    assert "已完成排列：共移动 5 个图标" in result

    # 米哈游列按给定顺序从上到下
    assert backend.pos_of("绝区零") == (START_X, START_Y)
    assert backend.pos_of("崩坏：星穹铁道") == (START_X, START_Y + ROW_H)
    assert backend.pos_of("原神") == (START_X, START_Y + 2 * ROW_H)
    # 未分组的进「其他」列，保持原相对顺序（回收站在 QQ 前）
    other_x = START_X + COL_W
    assert backend.pos_of("回收站") == (other_x, START_Y)
    assert backend.pos_of("QQ") == (other_x, START_Y + ROW_H)


def test_fuzzy_match_substring_case_insensitive() -> None:
    backend = FakeBackend(["Honkai: Star Rail"])
    result = ArrangeDesktopTool(backend=backend).execute(
        groups=[{"label": "游戏", "icons": ["star rail"]}],
        dry_run=True,
    )
    assert "Honkai: Star Rail" in result
    assert "警告" not in result


def test_unmatched_names_warn_and_ungrouped_go_to_other() -> None:
    backend = FakeBackend(GAMES)
    result = ArrangeDesktopTool(backend=backend).execute(
        groups=[{"label": "游戏", "icons": ["不存在的游戏", "绝区零"]}],
        dry_run=True,
    )
    assert "警告" in result and "不存在的游戏" in result
    # 未分组的图标进「其他」列，保持原相对顺序（原神、崩坏、回收站、QQ）
    assert "[其他]" in result
    other_x = START_X + COL_W
    assert f"原神 → ({other_x}, {START_Y})" in result
    assert f"回收站 → ({other_x}, {START_Y + 2 * ROW_H})" in result
    # 整组匹配不到时不占列：这里「游戏」组有命中，「其他」是第 1 列
    empty_only = ArrangeDesktopTool(backend=backend).execute(
        groups=[{"label": "空组", "icons": ["找不到"]}], dry_run=True
    )
    assert "[空组]" not in empty_only
    assert f"原神 → ({START_X}, {START_Y})" in empty_only  # 「其他」占第 0 列


def test_invalid_groups_rejected() -> None:
    tool = ArrangeDesktopTool(backend=FakeBackend(GAMES))
    with pytest.raises(ToolError, match="groups"):
        tool.execute(groups=None)
    with pytest.raises(ToolError, match="groups"):
        tool.execute(groups=[{"label": "缺 icons"}])


def test_backend_error_becomes_tool_error() -> None:
    class BrokenBackend:
        def find_desktop_listview(self) -> int:
            raise RuntimeError("boom")

    with pytest.raises(ToolError, match="操作桌面图标失败"):
        ListDesktopIconsTool(backend=BrokenBackend()).execute()
    with pytest.raises(ToolError, match="操作桌面图标失败"):
        ArrangeDesktopTool(backend=BrokenBackend()).execute(groups=MIHOYO, dry_run=False)

    # backend 主动抛的 ToolError 原样透传
    class ToolErrorBackend:
        def find_desktop_listview(self) -> int:
            raise ToolError("未找到桌面图标控件。")

    with pytest.raises(ToolError, match="未找到桌面图标控件"):
        ListDesktopIconsTool(backend=ToolErrorBackend()).execute()
