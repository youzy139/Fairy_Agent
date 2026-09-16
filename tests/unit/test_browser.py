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
