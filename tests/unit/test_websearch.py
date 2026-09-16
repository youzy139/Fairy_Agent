"""websearch.py 单元测试：HTML 解析与错误处理（桩掉网络请求）。"""

from __future__ import annotations

import pytest

from fairy.tools.base import ToolError
from fairy.tools.websearch import WebSearchTool

_FAKE_HTML = """
<html><body>
<li class="b_algo"><h2 class=""><a target="_blank" href="https://v.qq.com/s/topic/v_sports/render/uX0ceyb1.html"
>腾讯视频 F1 专题页</a></h2><p>摘要一</p></li>
<li class="b_algo"><h2 class=""><a target="_blank" href="https://example.com/f1-news"
>F1 <b>新闻</b></a></h2></li>
</body></html>
"""


def test_web_search_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fairy.tools.websearch.fetch_results",
        lambda query, limit: [
            ("腾讯视频 F1 专题页", "https://v.qq.com/s/topic/v_sports/render/uX0ceyb1.html"),
            ("F1 新闻", "https://example.com/f1-news"),
        ],
    )
    result = WebSearchTool().execute(query="腾讯视频 F1")
    assert "腾讯视频 F1 专题页" in result
    assert "https://v.qq.com/s/topic/v_sports/render/uX0ceyb1.html" in result


def test_web_search_html_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实走解析逻辑（桩掉 urllib 响应）。"""
    import urllib.request

    class _FakeResp:
        def read(self) -> bytes:
            return _FAKE_HTML.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp())

    from fairy.tools.websearch import fetch_results

    results = fetch_results("F1", 5)
    assert len(results) == 2
    assert results[0] == (
        "腾讯视频 F1 专题页",
        "https://v.qq.com/s/topic/v_sports/render/uX0ceyb1.html",
    )
    # 标题内的嵌套标签被剥掉
    assert results[1][0] == "F1 新闻"


def test_web_search_empty_query() -> None:
    with pytest.raises(ToolError, match="不能为空"):
        WebSearchTool().execute(query="  ")


def test_web_search_limit_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[int] = []

    def fake_fetch(query: str, limit: int) -> list[tuple[str, str]]:
        captured.append(limit)
        return []

    monkeypatch.setattr("fairy.tools.websearch.fetch_results", fake_fetch)
    WebSearchTool().execute(query="x", limit=99)
    assert captured == [10]


def test_web_search_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fairy.tools.websearch.fetch_results", lambda q, limit: [])
    result = WebSearchTool().execute(query="不存在的东西")
    assert "没有找到" in result
