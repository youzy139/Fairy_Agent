"""Windows 桌面图标 ListView 底层操作（仅 ctypes，无第三方依赖）。

操作桌面 SysListView32("FolderView") 控件，读取图标清单与坐标、设置图标
位置、开关自动排列。跨进程读图标文本使用标准技巧：OpenProcess 打开桌面
进程（explorer.exe），VirtualAllocEx 在对方进程分配缓冲区，SendMessageW
让控件把结果写入该缓冲区，ReadProcessMemory 读回，VirtualFreeEx 释放。

约定：桌面窗口与调用进程位数相同（同为 64 位），POINT/LVITEMW 结构布局一致。
所有 win32 调用检查返回值，失败抛 ToolError（中文原因）。仅 Windows 可用。

注意：真实桌面行为受系统版本 / 壁纸引擎影响，Win11 下 SHELLDLL_DefView
可能挂在 WorkerW 窗口而非 Progman 下，find_desktop_listview 两种情况都处理。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from fairy.tools.base import ToolError

# --- ListView 消息与样式常量 ---
_LVM_GETITEMCOUNT = 0x1004
_LVM_SETITEMPOSITION = 0x100F
_LVM_GETITEMPOSITION = 0x1010
_LVM_GETITEMTEXTW = 0x1073
_LVIF_TEXT = 0x0001
_GWL_STYLE = -16
_LVS_AUTOARRANGE = 0x0100

# --- 进程/内存常量 ---
_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020
_MEM_COMMIT = 0x1000
_MEM_RESERVE = 0x2000
_MEM_RELEASE = 0x8000
_PAGE_READWRITE = 0x04

# 图标名缓冲（wchar 数）
_TEXT_MAX = 512


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _LVITEMW(ctypes.Structure):
    # 64 位布局：pszText 用 c_void_p 以便赋远端进程地址整数
    _fields_ = [
        ("mask", wintypes.UINT),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", wintypes.UINT),
        ("stateMask", wintypes.UINT),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", wintypes.LPARAM),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", wintypes.UINT),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


def _load_dlls() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """加载 user32/kernel32 并设置原型（避免 64 位句柄/指针被 c_int 截断）。"""
    if sys.platform != "win32":
        raise ToolError("桌面图标操作仅支持 Windows。")
    user32 = ctypes.WinDLL("user32", use_errno=True)
    kernel32 = ctypes.WinDLL("kernel32", use_errno=True)

    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAllocEx.restype = wintypes.LPVOID
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.LPCVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    return user32, kernel32


def find_desktop_listview() -> int:
    """查找桌面图标 ListView 句柄，找不到时抛 ToolError。

    常规路径：Progman → SHELLDLL_DefView → SysListView32("FolderView")。
    Win11 壁纸分离场景下 SHELLDLL_DefView 可能挂在某个 WorkerW 窗口下，
    此时枚举顶层 WorkerW 窗口寻找。
    """
    user32, _ = _load_dlls()
    progman = user32.FindWindowW("Progman", "Program Manager")
    if not progman:
        raise ToolError("未找到 Progman 桌面窗口，桌面进程可能未运行。")

    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if not defview:
        # Win11 壁纸分离：枚举 WorkerW 窗口找含 SHELLDLL_DefView 的那个
        workerw = None
        while True:
            workerw = user32.FindWindowExW(None, workerw, "WorkerW", None)
            if not workerw:
                break
            defview = user32.FindWindowExW(workerw, None, "SHELLDLL_DefView", None)
            if defview:
                break
    if not defview:
        raise ToolError("未找到 SHELLDLL_DefView 桌面视图（Progman 与 WorkerW 均未命中）。")

    hwnd = user32.FindWindowExW(defview, None, "SysListView32", "FolderView")
    if not hwnd:
        raise ToolError("未找到桌面图标控件 SysListView32(FolderView)。")
    return int(hwnd)


class _RemoteBuffer:
    """桌面进程内的共享内存缓冲区（分配/读写/释放，含句柄清理）。"""

    def __init__(self, hwnd: int, size: int) -> None:
        self._user32, self._kernel32 = _load_dlls()
        pid = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid)):
            raise ToolError("获取桌面窗口所属进程 ID 失败。")
        access = _PROCESS_VM_OPERATION | _PROCESS_VM_READ | _PROCESS_VM_WRITE
        self._process = self._kernel32.OpenProcess(access, False, pid.value)
        if not self._process:
            raise ToolError(f"打开桌面进程失败（错误码 {ctypes.get_last_error()}）。")
        self.address = self._kernel32.VirtualAllocEx(
            self._process, None, size, _MEM_COMMIT | _MEM_RESERVE, _PAGE_READWRITE
        )
        if not self.address:
            self._kernel32.CloseHandle(self._process)
            raise ToolError(f"在桌面进程分配共享内存失败（错误码 {ctypes.get_last_error()}）。")

    def write(self, data: bytes) -> None:
        written = ctypes.c_size_t()
        if not self._kernel32.WriteProcessMemory(
            self._process, self.address, data, len(data), ctypes.byref(written)
        ):
            raise ToolError(f"向桌面进程写入数据失败（错误码 {ctypes.get_last_error()}）。")

    def read(self, offset: int, size: int) -> bytes:
        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t()
        if not self._kernel32.ReadProcessMemory(
            self._process, self.address + offset, buf, size, ctypes.byref(read)
        ):
            raise ToolError(f"从桌面进程读取数据失败（错误码 {ctypes.get_last_error()}）。")
        return bytes(buf)

    def close(self) -> None:
        self._kernel32.VirtualFreeEx(self._process, self.address, 0, _MEM_RELEASE)
        self._kernel32.CloseHandle(self._process)


def list_icons(hwnd: int) -> list[dict]:
    """列出桌面全部图标，每项 {index, name, x, y}。"""
    user32, _ = _load_dlls()
    count = user32.SendMessageW(wintypes.HWND(hwnd), _LVM_GETITEMCOUNT, 0, 0)
    if count < 0:
        raise ToolError("读取桌面图标数量失败。")

    buf = _RemoteBuffer(hwnd, ctypes.sizeof(_LVITEMW) + _TEXT_MAX * 2)
    try:
        icons: list[dict] = []
        for i in range(count):
            # 坐标：LVM_GETITEMPOSITION 把 POINT 写入远端缓冲区
            ok = user32.SendMessageW(wintypes.HWND(hwnd), _LVM_GETITEMPOSITION, i, buf.address)
            if not ok:
                raise ToolError(f"读取第 {i} 个桌面图标的坐标失败。")
            point = _POINT.from_buffer_copy(buf.read(0, ctypes.sizeof(_POINT)))

            # 文本：LVITEMW.pszText 指向远端缓冲区的文本区
            item = _LVITEMW()
            item.mask = _LVIF_TEXT
            item.iItem = i
            item.iSubItem = 0
            item.pszText = buf.address + ctypes.sizeof(_LVITEMW)
            item.cchTextMax = _TEXT_MAX
            buf.write(bytes(item))
            user32.SendMessageW(wintypes.HWND(hwnd), _LVM_GETITEMTEXTW, i, buf.address)
            raw = buf.read(ctypes.sizeof(_LVITEMW), _TEXT_MAX * 2)
            name = raw.decode("utf-16-le", errors="replace").split("\x00")[0]
            icons.append({"index": i, "name": name, "x": point.x, "y": point.y})
        return icons
    finally:
        buf.close()


def set_icon_position(hwnd: int, index: int, x: int, y: int) -> None:
    """把第 index 个图标移动到 (x, y)，失败抛 ToolError。"""
    user32, _ = _load_dlls()
    lparam = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
    ok = user32.SendMessageW(wintypes.HWND(hwnd), _LVM_SETITEMPOSITION, index, lparam)
    if not ok:
        raise ToolError(f"移动第 {index} 个桌面图标到 ({x}, {y}) 失败。")


def is_auto_arrange(hwnd: int) -> bool:
    """桌面是否开启「自动排列图标」。"""
    return bool(_get_style(hwnd) & _LVS_AUTOARRANGE)


def set_auto_arrange(hwnd: int, enabled: bool) -> None:
    """开关「自动排列图标」，失败抛 ToolError。"""
    user32, _ = _load_dlls()
    style = _get_style(hwnd)
    new_style = (style | _LVS_AUTOARRANGE) if enabled else (style & ~_LVS_AUTOARRANGE)
    if new_style == style:
        return
    ctypes.set_last_error(0)
    old = user32.SetWindowLongW(wintypes.HWND(hwnd), _GWL_STYLE, new_style)
    if old == 0 and ctypes.get_last_error() != 0:
        raise ToolError(
            f"{'开启' if enabled else '关闭'}桌面自动排列失败（错误码 {ctypes.get_last_error()}）。"
        )


def _get_style(hwnd: int) -> int:
    user32, _ = _load_dlls()
    ctypes.set_last_error(0)
    style = user32.GetWindowLongW(wintypes.HWND(hwnd), _GWL_STYLE)
    if style == 0 and ctypes.get_last_error() != 0:
        raise ToolError(f"读取桌面窗口样式失败（错误码 {ctypes.get_last_error()}）。")
    return style
