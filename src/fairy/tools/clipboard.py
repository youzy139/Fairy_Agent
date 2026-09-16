"""剪贴板工具：clipboard_read / clipboard_write（仅 Windows，ctypes 实现）。

读：OpenClipboard → GetClipboardData(CF_UNICODETEXT) → GlobalLock → 读宽字符串。
写：OpenClipboard → EmptyClipboard → GlobalAlloc(GMEM_MOVEABLE) → GlobalLock →
memcpy → SetClipboardData。注意 SetClipboardData 成功后内存由系统接管，
不得再 GlobalFree；仅失败路径需要释放。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x2002

_user32: Any = None
_kernel32: Any = None


def _ensure_windows() -> None:
    if sys.platform != "win32":
        raise ToolError("暂未支持该平台")


def _apis() -> tuple[Any, Any]:
    """惰性初始化 user32/kernel32 句柄并声明参数/返回类型（防 64 位指针截断）。"""
    global _user32, _kernel32
    _ensure_windows()
    if _user32 is None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
        kernel32.GlobalFree.restype = wintypes.HANDLE
        _user32, _kernel32 = user32, kernel32
    return _user32, _kernel32


def _read_text() -> str:
    """读取剪贴板中的 Unicode 文本；无文本时抛 ToolError。"""
    user32, kernel32 = _apis()
    if not user32.OpenClipboard(None):
        raise ToolError("无法打开剪贴板（可能被其他程序占用）。")
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            raise ToolError("剪贴板中没有文本内容。")
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            raise ToolError("读取剪贴板数据失败（GetClipboardData 返回空句柄）。")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise ToolError("锁定剪贴板数据失败（GlobalLock 返回空指针）。")
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _write_text(text: str) -> None:
    """把 Unicode 文本写入剪贴板（覆盖原内容）。"""
    user32, kernel32 = _apis()
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        raise ToolError("无法打开剪贴板（可能被其他程序占用）。")
    try:
        if not user32.EmptyClipboard():
            raise ToolError("清空剪贴板失败。")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise ToolError("GlobalAlloc 分配内存失败。")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            raise ToolError("GlobalLock 锁定内存失败。")
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        # SetClipboardData 成功后内存由系统接管，不得 GlobalFree
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ToolError("SetClipboardData 写入剪贴板失败。")
    finally:
        user32.CloseClipboard()


class ClipboardReadTool(Tool):
    """读取系统剪贴板中的文本（read 级权限）。"""

    name = "clipboard_read"
    description = "读取系统剪贴板中的文本内容"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission: PermissionLevel = "read"

    def execute(self) -> str:
        text = _read_text()
        return text if text else "（剪贴板为空）"


class ClipboardWriteTool(Tool):
    """把文本写入系统剪贴板（write 级权限，覆盖原内容需确认）。"""

    name = "clipboard_write"
    description = "把指定文本写入系统剪贴板（覆盖原有内容）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要写入剪贴板的文本"},
        },
        "required": ["text"],
    }
    permission: PermissionLevel = "write"

    def execute(self, text: str) -> str:
        _write_text(text)
        return f"已写入剪贴板（{len(text)} 字符）。"
