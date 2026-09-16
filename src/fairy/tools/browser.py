"""浏览器工具：browser_open（network 级权限）。

仅允许 http/https 协议，拒绝 file:/javascript: 等其他 scheme，防止滥用。
支持网址收藏：传入名字（如「B站」）时先查内置收藏表，再查记忆层
``fav:<名字>``（由 remember 工具写入），解析成网址后打开。
"""

from __future__ import annotations

import webbrowser
from typing import Any
from urllib.parse import urlsplit

from fairy.tools.base import PermissionLevel, Tool, ToolError

_ALLOWED_SCHEMES = ("http", "https")

# 内置常用网站收藏（用户可通过 remember 工具扩充/覆盖到记忆层）
_BUILTIN_FAVORITES: dict[str, str] = {
    "b站": "https://www.bilibili.com",
    "bilibili": "https://www.bilibili.com",
    "github": "https://github.com",
    "知乎": "https://www.zhihu.com",
    "微博": "https://weibo.com",
    "百度": "https://www.baidu.com",
    "谷歌": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "kimi": "https://www.kimi.com",
}


class BrowserOpenTool(Tool):
    """用默认浏览器打开网址或收藏名。"""

    name = "browser_open"
    description = (
        "用系统默认浏览器打开网址（仅支持 http/https）。"
        "也可以直接传收藏名（如「B站」「github」），会自动解析成对应网址；"
        "用户可用 remember 工具新增收藏。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要打开的 http/https 网址，或收藏名（如「B站」）",
            },
        },
        "required": ["url"],
    }
    permission: PermissionLevel = "network"

    def __init__(self, memory: Any = None) -> None:
        # memory 为 MemoryStore；None 时仅用内置收藏表
        self._memory = memory

    def _resolve(self, text: str) -> str:
        """把输入解析为网址：已是 http/https 原样返回；否则按收藏名查表。"""
        scheme = urlsplit(text).scheme.lower()
        if scheme in _ALLOWED_SCHEMES:
            return text
        if scheme:
            raise ToolError(f"仅允许打开 http/https 链接，已拒绝：{text!r}")

        # 无 scheme：按收藏名解析（记忆层优先于内置表）
        key = text.lower()
        if self._memory is not None:
            remembered = self._memory.get_preference(f"fav:{key}")
            if remembered:
                return remembered
        if key in _BUILTIN_FAVORITES:
            return _BUILTIN_FAVORITES[key]
        # 用户省略了协议头的裸域名
        if "." in text and " " not in text:
            return "https://" + text
        raise ToolError(
            f"无法识别 {text!r}：不是网址也不在收藏表。"
            "可以对我说「记住 {0} 是 <网址>」让我学会。".format(text)
        )

    def execute(self, url: str) -> str:
        resolved = self._resolve(url.strip())
        if webbrowser.open(resolved):
            return f"已在默认浏览器打开：{resolved}"
        return f"已尝试调用默认浏览器，但系统未确认打开成功：{resolved}"
