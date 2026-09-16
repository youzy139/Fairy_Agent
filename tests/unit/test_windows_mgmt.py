"""windows_mgmt.py 单元测试：窗口枚举、激活与最小化逻辑。

win32 层 ctypes 调用全部 monkeypatch 成桩（EnumWindows 回调注入假窗口句柄），
不操作真实窗口。仅在 Windows 上运行（非 Windows 分支用例除外）。
"""

from __future__ import annotations

import sys

import pytest

import fairy.tools.windows_mgmt as wm
from fairy.tools.base import ToolError
from fairy.tools.windows_mgmt import (
    SW_MINIMIZE,
    SW_RESTORE,
    ActivateWindowTool,
    ListWindowsTool,
    MinimizeAllWindowsTool,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ctypes 桩测试仅 Windows")

FAKE_WINDOWS = [(101, "鸣潮"), (102, "kimi-desktop-pet - Visual Studio Code"), (103, "微信")]


def _stub_visible(monkeypatch: pytest.MonkeyPatch, windows: list[tuple[int, str]]) -> None:
    monkeypatch.setattr(wm, "_enum_visible_windows", lambda: list(windows))


# ------------------------------------------------------------------
# EnumWindows 回调注入
# ------------------------------------------------------------------


def test_enum_window_handles_via_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_enum_windows(callback: object, lparam: int) -> bool:
        for hwnd in (11, 22, 33):
            callback(hwnd, lparam)  # type: ignore[operator]
        return True

    monkeypatch.setattr(wm.user32, "EnumWindows", fake_enum_windows)
    assert wm._enum_window_handles() == [11, 22, 33]


def test_enum_visible_windows_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """不可见、桌面外壳与空标题窗口都被过滤。"""
    monkeypatch.setattr(wm, "_enum_window_handles", lambda: [1, 2, 3, 4])
    visible = {1: True, 2: True, 3: True, 4: False}
    monkeypatch.setattr(wm.user32, "IsWindowVisible", lambda hwnd: visible[hwnd])
    classes = {1: "Progman", 2: "Chrome_WidgetWin_1", 3: "Notepad"}
    monkeypatch.setattr(wm, "_window_class", lambda hwnd: classes[hwnd])
    titles = {2: "  浏览器  ", 3: ""}
    monkeypatch.setattr(wm, "_window_title", lambda hwnd: titles.get(hwnd, ""))
    assert wm._enum_visible_windows() == [(2, "浏览器")]


# ------------------------------------------------------------------
# list_windows
# ------------------------------------------------------------------


def test_list_windows_numbered(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_visible(monkeypatch, FAKE_WINDOWS)
    result = ListWindowsTool().execute()
    assert result.splitlines() == [
        "1. 鸣潮",
        "2. kimi-desktop-pet - Visual Studio Code",
        "3. 微信",
    ]


def test_list_windows_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_visible(monkeypatch, [])
    assert ListWindowsTool().execute() == "（没有可见窗口）"


# ------------------------------------------------------------------
# activate_window
# ------------------------------------------------------------------


def test_activate_window_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_visible(monkeypatch, FAKE_WINDOWS)
    shown: list[tuple[int, int]] = []
    foreground: list[int] = []
    monkeypatch.setattr(
        wm.user32, "ShowWindow", lambda hwnd, cmd: shown.append((hwnd, cmd)) or True
    )
    monkeypatch.setattr(wm, "_force_foreground", lambda hwnd: foreground.append(hwnd))
    result = ActivateWindowTool().execute("visual studio")
    assert shown == [(102, SW_RESTORE)]
    assert foreground == [102]
    assert "Visual Studio Code" in result


def test_activate_window_multiple_matches_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = [(201, "微信"), (202, "微信（工作版）")]
    _stub_visible(monkeypatch, windows)
    foreground: list[int] = []
    monkeypatch.setattr(wm.user32, "ShowWindow", lambda hwnd, cmd: True)
    monkeypatch.setattr(wm, "_force_foreground", lambda hwnd: foreground.append(hwnd))
    result = ActivateWindowTool().execute("微信")
    assert foreground == [201]
    assert "共 2 个匹配" in result


def test_activate_window_not_found_lists_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_visible(monkeypatch, FAKE_WINDOWS)
    with pytest.raises(ToolError) as exc_info:
        ActivateWindowTool().execute("钉钉")
    message = str(exc_info.value)
    assert "找不到" in message
    assert "鸣潮" in message and "微信" in message


def test_force_foreground_attach_thread_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """SetForegroundWindow 首次失败时，走 AttachThreadInput 附加线程后重试。"""
    set_calls: list[int] = []
    attach_calls: list[tuple[int, int, bool]] = []

    def fake_set_foreground(hwnd: int) -> bool:
        set_calls.append(hwnd)
        return len(set_calls) > 1  # 第一次失败，重试成功

    monkeypatch.setattr(wm.user32, "SetForegroundWindow", fake_set_foreground)
    monkeypatch.setattr(wm.user32, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(wm.kernel32, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(wm.user32, "GetWindowThreadProcessId", lambda hwnd, pid: 2)
    monkeypatch.setattr(
        wm.user32,
        "AttachThreadInput",
        lambda cur, tid, attach: attach_calls.append((cur, tid, attach)) or True,
    )
    wm._force_foreground(102)
    assert set_calls == [102, 102]  # 失败后重试了一次
    # 前台线程与目标线程都执行了 attach/detach 配对
    assert (1, 2, True) in attach_calls
    assert (1, 2, False) in attach_calls
    assert len(attach_calls) == 4


# ------------------------------------------------------------------
# minimize_all_windows
# ------------------------------------------------------------------


def test_minimize_all_skips_shell_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wm, "_enum_window_handles", lambda: [1, 2, 3, 4])
    monkeypatch.setattr(wm.user32, "IsWindowVisible", lambda hwnd: hwnd != 4)
    classes = {1: "Progman", 2: "Chrome_WidgetWin_1", 3: "Notepad", 4: "WorkerW"}
    monkeypatch.setattr(wm, "_window_class", lambda hwnd: classes[hwnd])
    minimized: list[tuple[int, int]] = []
    monkeypatch.setattr(
        wm.user32, "ShowWindow", lambda hwnd, cmd: minimized.append((hwnd, cmd)) or True
    )
    result = MinimizeAllWindowsTool().execute()
    # 1 是 Progman（桌面）、4 不可见，均被跳过
    assert minimized == [(2, SW_MINIMIZE), (3, SW_MINIMIZE)]
    assert "2 个窗口" in result


# ------------------------------------------------------------------
# 非 Windows 分支（不依赖真实平台）
# ------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 上有 user32 桩对象")
def test_non_windows_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ToolError, match="暂未支持该平台"):
        ListWindowsTool().execute()
    with pytest.raises(ToolError, match="暂未支持该平台"):
        ActivateWindowTool().execute("x")
    with pytest.raises(ToolError, match="暂未支持该平台"):
        MinimizeAllWindowsTool().execute()
