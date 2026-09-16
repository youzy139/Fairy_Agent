"""网页搜索工具：web_search（network 级）。

用 Bing 的 HTML 结果页做轻量搜索（无 API Key 依赖），返回标题 + 链接列表。
典型场景：用户说「打开腾讯视频，我要看 F1」——先搜索「腾讯视频 F1」找到
准确的专题页，再用 browser_open 打开，而不是猜一个搜索页地址。

注意：HTML 解析依赖 Bing 的页面结构，结构变更会导致解析失败（报错而非乱给结果）。
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

_SEARCH_URL = "https://www.bing.com/search"
_TIMEOUT_SECONDS = 15
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Bing 结果条目：<h2 ...><a ... href="...">标题</a></h2>（h2 与 a 都可能带属性）
_RESULT_PATTERN = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_PATTERN.sub("", html).strip()


def fetch_results(query: str, limit: int) -> list[tuple[str, str]]:
    """请求 Bing 并解析结果，返回 [(标题, 链接)]。可注入桩测试。"""
    url = _SEARCH_URL + "?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"搜索请求失败：{exc}") from exc

    results: list[tuple[str, str]] = []
    for href, title_html in _RESULT_PATTERN.findall(html):
        title = _strip_tags(title_html)
        if title and href.startswith("http"):
            results.append((title, href))
        if len(results) >= limit:
            break
    return results


class WebSearchTool(Tool):
    """搜索网页，返回标题与链接列表（供 browser_open 选用准确页面）。"""

    name = "web_search"
    description = (
        "搜索网页，返回最相关的标题与链接。当用户想打开某个网站的特定内容"
        "（如「腾讯视频看 F1」）时，先搜索找到准确页面再用 browser_open 打开，"
        "不要自己猜 URL。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回条数，默认 5，最多 10"},
        },
        "required": ["query"],
    }
    permission: PermissionLevel = "network"

    def execute(self, query: str, limit: int = 5) -> str:
        query = query.strip()
        if not query:
            raise ToolError("搜索关键词不能为空。")
        limit = max(1, min(int(limit), 10))

        results = fetch_results(query, limit)
        if not results:
            return f"没有找到「{query}」的相关结果（可能是搜索引擎页面结构变化或被拦截）。"

        lines = [f"「{query}」的搜索结果："]
        for i, (title, href) in enumerate(results, start=1):
            lines.append(f"{i}. {title}\n   {href}")
        return "\n".join(lines)
