"""clipboard.py 单元测试：真实剪贴板读写往返 + 非 Windows 分支。

真实剪贴板测试仅在 Windows 上运行；测试结束恢复原剪贴板文本内容。
"""

from __future__ import annotations

import sys

import pytest

from fairy.tools.base import ToolError
from fairy.tools.clipboard import ClipboardReadTool, ClipboardWriteTool, _read_text


@pytest.mark.skipif(sys.platform != "win32", reason="真实剪贴板测试仅 Windows")
def test_write_then_read_roundtrip() -> None:
    write_tool = ClipboardWriteTool()
    read_tool = ClipboardReadTool()
    try:
        original: str | None = _read_text()
    except ToolError:
        original = None  # 原剪贴板不是文本，无法恢复
    payload = "Fairy 剪贴板往返测试 123 ✨"
    try:
        result = write_tool.execute(text=payload)
        assert "已写入剪贴板" in result
        assert read_tool.execute() == payload
    finally:
        if original is not None:
            write_tool.execute(text=original)


@pytest.mark.skipif(sys.platform != "win32", reason="真实剪贴板测试仅 Windows")
def test_write_empty_string() -> None:
    write_tool = ClipboardWriteTool()
    try:
        original: str | None = _read_text()
    except ToolError:
        original = None
    try:
        write_tool.execute(text="")
        assert ClipboardReadTool().execute() == "（剪贴板为空）"
    finally:
        if original is not None:
            write_tool.execute(text=original)


def test_non_windows_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ToolError, match="暂未支持该平台"):
        ClipboardReadTool().execute()
    with pytest.raises(ToolError, match="暂未支持该平台"):
        ClipboardWriteTool().execute(text="x")
