"""browser.py 单元测试：协议白名单与 webbrowser.open 调用。"""

from __future__ import annotations

import webbrowser

import pytest

from fairy.tools.base import ToolError
from fairy.tools.browser import BrowserOpenTool


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """拦截 webbrowser.open，返回记录 URL 的列表。"""
    calls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: calls.append(url) or True)
    return calls


def test_open_https(opened: list[str]) -> None:
    result = BrowserOpenTool().execute("https://example.com")
    assert opened == ["https://example.com"]
    assert "https://example.com" in result


def test_open_http(opened: list[str]) -> None:
    BrowserOpenTool().execute("http://localhost:8080/docs")
    assert opened == ["http://localhost:8080/docs"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32",
        "javascript:alert(1)",
        "ftp://example.com",
        "C:\\Windows\\notepad.exe",
        "vbscript:msgbox(1)",
    ],
)
def test_reject_non_http_schemes(url: str, opened: list[str]) -> None:
    with pytest.raises(ToolError, match="http"):
        BrowserOpenTool().execute(url)
    assert opened == []


def test_open_failure_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    result = BrowserOpenTool().execute("https://example.com")
    assert "未确认打开成功" in result


def test_site_search_builtin_template(opened: list[str]) -> None:
    """「打开B站我要学rag」→ B站站内搜索页。"""
    result = BrowserOpenTool().execute("b站", query="rag")
    assert opened == ["https://search.bilibili.com/all?keyword=rag"]
    assert "站内搜索" in result


def test_site_search_by_domain(opened: list[str]) -> None:
    """传完整网址时按域名匹配搜索模板。"""
    BrowserOpenTool().execute("https://www.zhihu.com", query="fairy agent")
    assert opened == ["https://www.zhihu.com/search?type=content&q=fairy%20agent"]


def test_site_search_query_encoded(opened: list[str]) -> None:
    """关键词需要 URL 编码。"""
    BrowserOpenTool().execute("github", query="桌面 宠物")
    assert opened[0] == "https://github.com/search?q=%E6%A1%8C%E9%9D%A2%20%E5%AE%A0%E7%89%A9"


def test_site_search_unknown_site_rejected(opened: list[str]) -> None:
    """无搜索模板的站点报错并指引 web_search。"""
    with pytest.raises(ToolError, match="web_search"):
        BrowserOpenTool().execute("某个冷门网站", query="rag")
    assert opened == []


def test_no_query_unchanged(opened: list[str]) -> None:
    """不带 query 时行为不变：收藏名直达首页。"""
    BrowserOpenTool().execute("b站")
    assert opened == ["https://www.bilibili.com"]
