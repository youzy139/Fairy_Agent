"""窗口管理工具：list_windows / activate_window / minimize_all_windows。

仅 Windows，用 ctypes 调 user32：EnumWindows 枚举顶层窗口，
IsWindowVisible / GetWindowTextW 过滤可见窗口，ShowWindow 最小化或还原，
SetForegroundWindow 激活（失败时用 AttachThreadInput 附加前台线程后重试）。
"""

from __future__ import annotations

import sys
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

SW_MINIMIZE = 6
SW_RESTORE = 9

# 桌面外壳窗口的类名，枚举/最小化时跳过
_SHELL_CLASSES = ("Progman", "WorkerW")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32: Any = ctypes.windll.user32
    kernel32: Any = ctypes.windll.kernel32

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # 声明参数/返回类型，避免 64 位指针被默认 c_int 截断
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:
    user32 = None
    kernel32 = None
    EnumWindowsProc = None


def _ensure_windows() -> None:
    if sys.platform != "win32" or user32 is None:
        raise ToolError("暂未支持该平台")


def _enum_window_handles() -> list[int]:
    """EnumWindows 枚举全部顶层窗口句柄。"""

    handles: list[int] = []

    @EnumWindowsProc
    def _callback(hwnd: int, lparam: int) -> bool:
        handles.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return handles


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _enum_visible_windows() -> list[tuple[int, str]]:
    """枚举可见且有标题的顶层窗口（跳过桌面外壳窗口），返回 (hwnd, 标题)。"""
    result: list[tuple[int, str]] = []
    for hwnd in _enum_window_handles():
        if not user32.IsWindowVisible(hwnd):
            continue
        if _window_class(hwnd) in _SHELL_CLASSES:
            continue
        title = _window_title(hwnd).strip()
        if title:
            result.append((hwnd, title))
    return result


def _force_foreground(hwnd: int) -> None:
    """激活窗口到前台；SetForegroundWindow 失败时用 AttachThreadInput 重试。"""
    if user32.SetForegroundWindow(hwnd):
        return
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return
    current_tid = kernel32.GetCurrentThreadId()
    foreground_tid = user32.GetWindowThreadProcessId(foreground, None)
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached: list[int] = []
    try:
        for tid in (foreground_tid, target_tid):
            if tid and tid != current_tid and user32.AttachThreadInput(current_tid, tid, True):
                attached.append(tid)
        user32.SetForegroundWindow(hwnd)
    finally:
        for tid in attached:
            user32.AttachThreadInput(current_tid, tid, False)


class ListWindowsTool(Tool):
    """列出当前可见的顶层窗口（read 级权限）。"""

    name = "list_windows"
    description = "列出当前所有可见窗口的编号与标题"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission: PermissionLevel = "read"

    def execute(self) -> str:
        _ensure_windows()
        windows = _enum_visible_windows()
        if not windows:
            return "（没有可见窗口）"
        return "\n".join(f"{i}. {title}" for i, (_, title) in enumerate(windows, 1))


class ActivateWindowTool(Tool):
    """按标题子串激活（置前台）窗口（write 级权限）。"""

    name = "activate_window"
    description = "激活标题包含指定文字的窗口（还原并置于前台）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title_substr": {"type": "string", "description": "窗口标题的子串（大小写不敏感）"},
        },
        "required": ["title_substr"],
    }
    permission: PermissionLevel = "write"

    def execute(self, title_substr: str) -> str:
        _ensure_windows()
        needle = title_substr.strip().lower()
        if not needle:
            raise ToolError("标题子串不能为空。")
        windows = _enum_visible_windows()
        matches = [(hwnd, title) for hwnd, title in windows if needle in title.lower()]
        if not matches:
            candidates = "、".join(title for _, title in windows[:10]) or "（无可见窗口）"
            raise ToolError(f"找不到标题包含「{title_substr}」的窗口。当前可见窗口：{candidates}")
        hwnd, title = matches[0]
        user32.ShowWindow(hwnd, SW_RESTORE)
        _force_foreground(hwnd)
        extra = f"（共 {len(matches)} 个匹配，已激活第一个）" if len(matches) > 1 else ""
        return f"已激活窗口：{title}{extra}"


class MinimizeAllWindowsTool(Tool):
    """最小化所有可见顶层窗口（write 级权限，跳过桌面外壳窗口）。"""

    name = "minimize_all_windows"
    description = "最小化所有可见窗口（效果等同显示桌面）"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission: PermissionLevel = "write"

    def execute(self) -> str:
        _ensure_windows()
        count = 0
        for hwnd in _enum_window_handles():
            if not user32.IsWindowVisible(hwnd):
                continue
            if _window_class(hwnd) in _SHELL_CLASSES:
                continue
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            count += 1
        return f"已最小化 {count} 个窗口。"
