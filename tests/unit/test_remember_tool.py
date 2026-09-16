"""remember.py 单元测试：别名/收藏/偏好的写入与 browser_open 收藏解析联动。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.memory.store import MemoryStore
from fairy.tools.base import ToolError
from fairy.tools.browser import BrowserOpenTool
from fairy.tools.remember import RememberTool


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path)
    yield store
    store.close()


def test_remember_site(memory: MemoryStore) -> None:
    tool = RememberTool(memory)
    result = tool.execute(kind="site", name="B站", target="https://www.bilibili.com")
    assert "已记住" in result
    assert memory.get_preference("fav:b站") == "https://www.bilibili.com"


def test_remember_app_alias(memory: MemoryStore) -> None:
    RememberTool(memory).execute(kind="app", name="鸣潮", target="D:/games/ww.exe")
    assert memory.get_preference("alias:鸣潮") == "D:/games/ww.exe"


def test_remember_invalid_kind(memory: MemoryStore) -> None:
    with pytest.raises(ToolError, match="未知记忆类型"):
        RememberTool(memory).execute(kind="other", name="x", target="y")


def test_remember_empty_rejected(memory: MemoryStore) -> None:
    with pytest.raises(ToolError, match="不能为空"):
        RememberTool(memory).execute(kind="site", name=" ", target="x")


def test_remember_without_memory() -> None:
    with pytest.raises(ToolError, match="记忆层未启用"):
        RememberTool(None).execute(kind="site", name="x", target="y")


def test_remember_auto_allow(memory: MemoryStore) -> None:
    """只写自身记忆库，免确认。"""
    assert RememberTool(memory).auto_allow({}) is True


def test_browser_resolves_remembered_favorite(
    memory: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """browser_open 能用 remember 存下的收藏名。"""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    RememberTool(memory).execute(kind="site", name="B站", target="https://www.bilibili.com")
    result = BrowserOpenTool(memory).execute(url="b站")

    assert opened == ["https://www.bilibili.com"]
    assert "bilibili.com" in result


def test_browser_builtin_favorite(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    BrowserOpenTool().execute(url="github")
    assert opened == ["https://github.com"]


def test_browser_bare_domain_gets_https(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    BrowserOpenTool().execute(url="example.com")
    assert opened == ["https://example.com"]


def test_browser_unknown_name_hint() -> None:
    with pytest.raises(ToolError, match="记住"):
        BrowserOpenTool().execute(url="某个没听说过的网站")


def test_browser_bad_scheme_still_rejected() -> None:
    with pytest.raises(ToolError, match="仅允许"):
        BrowserOpenTool().execute(url="file:///etc/passwd")
