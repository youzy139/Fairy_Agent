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

# 站点搜索页模板（键为收藏名或域名，{q} 为关键词占位）
# 「打开X看Y」时优先用站内搜索页，而不是只开首页
_SEARCH_TEMPLATES: dict[str, str] = {
    "b站": "https://search.bilibili.com/all?keyword={q}",
    "bilibili": "https://search.bilibili.com/all?keyword={q}",
    "bilibili.com": "https://search.bilibili.com/all?keyword={q}",
    "github": "https://github.com/search?q={q}",
    "github.com": "https://github.com/search?q={q}",
    "知乎": "https://www.zhihu.com/search?type=content&q={q}",
    "zhihu.com": "https://www.zhihu.com/search?type=content&q={q}",
    "微博": "https://s.weibo.com/weibo?q={q}",
    "weibo.com": "https://s.weibo.com/weibo?q={q}",
    "百度": "https://www.baidu.com/s?wd={q}",
    "baidu.com": "https://www.baidu.com/s?wd={q}",
    "谷歌": "https://www.google.com/search?q={q}",
    "google.com": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "youtube.com": "https://www.youtube.com/results?search_query={q}",
}


class BrowserOpenTool(Tool):
    """用默认浏览器打开网址或收藏名。"""

    name = "browser_open"
    description = (
        "用系统默认浏览器打开网址（仅支持 http/https）。"
        "可以传收藏名（如「B站」「github」）自动解析成网址；"
        "带 query 参数时打开该站点的站内搜索页（如 url=B站, query=rag → B站搜索 rag），"
        "这是「打开某网站看/学某内容」的默认做法；站点无搜索模板时改用 web_search。"
        "用户可用 remember 工具新增收藏。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要打开的 http/https 网址，或收藏名（如「B站」）",
            },
            "query": {
                "type": "string",
                "description": "可选：站内搜索关键词。给出时打开该站点的搜索结果页",
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

    def _resolve_site_key(self, text: str) -> str:
        """把收藏名/网址归一为站点键（用于查搜索模板）。"""
        key = text.strip().lower()
        if key in _SEARCH_TEMPLATES:
            return key
        # 收藏名 → 网址 → 域名 → 模板
        try:
            resolved = self._resolve(text)
        except ToolError:
            resolved = ""
        if resolved:
            domain = urlsplit(resolved).netloc.lower().removeprefix("www.")
            if domain in _SEARCH_TEMPLATES:
                return domain
        raise ToolError(
            f"站点 {text!r} 没有内置搜索模板。请改用 web_search 搜索后打开准确页面，"
            "或对我说「记住 <名字> 是 <网址>」建立直达收藏。"
        )

    def execute(self, url: str, query: str | None = None) -> str:
        import urllib.parse

        if query and query.strip():
            key = self._resolve_site_key(url)
            target = _SEARCH_TEMPLATES[key].format(q=urllib.parse.quote(query.strip()))
            if webbrowser.open(target):
                return f"已打开 {url} 的站内搜索「{query.strip()}」：{target}"
            return f"已尝试调用浏览器，但系统未确认打开成功：{target}"

        resolved = self._resolve(url.strip())
        if webbrowser.open(resolved):
            return f"已在默认浏览器打开：{resolved}"
        return f"已尝试调用默认浏览器，但系统未确认打开成功：{resolved}"
