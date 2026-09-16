"""apps.py 单元测试：open_app 的别名记忆、lnk 搜索、多候选选择与白名单。

启动动作（os.startfile）一律 monkeypatch 拦截，不真启动程序；
注册表查询统一桩掉，避免依赖测试机真实环境。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import fairy.tools.apps as apps_mod
from fairy.memory.store import MemoryStore
from fairy.tools.apps import OpenAppTool
from fairy.tools.base import ToolError


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """拦截 os.startfile，返回记录启动参数的列表。"""
    calls: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)
    return calls


@pytest.fixture(autouse=True)
def _stub_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """桩掉注册表 App Paths 查询，避免命中测试机真实注册表。"""
    monkeypatch.setattr(apps_mod, "_registry_app_paths", lambda name: None)


def _setup_lnk_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str]) -> Path:
    """造一个假桌面目录，放置指定名字的 .lnk，并把搜索目录指过去。"""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    for name in names:
        (desktop / f"{name}.lnk").write_text("fake lnk", encoding="utf-8")
    monkeypatch.setattr(apps_mod, "_lnk_search_dirs", lambda: [desktop])
    return desktop


# ------------------------------------------------------------------
# 别名记忆
# ------------------------------------------------------------------


def test_alias_hit_launches_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    fake_lnk = tmp_path / "WutheringWaves.lnk"
    fake_lnk.write_text("fake", encoding="utf-8")
    store = MemoryStore(tmp_path / "mem")
    store.set_preference("alias:鸣潮", str(fake_lnk))
    # 搜索目录置空，确保命中的是别名而非搜索
    monkeypatch.setattr(apps_mod, "_lnk_search_dirs", lambda: [])
    try:
        result = OpenAppTool(memory=store).execute("鸣潮")
    finally:
        store.close()
    assert launched == [str(fake_lnk)]
    assert "别名记忆" in result


def test_stale_alias_falls_back_to_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    """别名指向的文件已不存在时，回退到搜索并覆盖旧别名。"""
    desktop = _setup_lnk_dirs(tmp_path, monkeypatch, ["WeChat"])
    store = MemoryStore(tmp_path / "mem")
    store.set_preference("alias:wechat", str(tmp_path / "已删除.lnk"))
    try:
        result = OpenAppTool(memory=store).execute("WeChat")
        assert store.get_preference("alias:wechat") == str(desktop / "WeChat.lnk")
    finally:
        store.close()
    assert launched == [str(desktop / "WeChat.lnk")]
    assert "已启动" in result


def test_alias_saved_after_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    """搜索命中并启动成功后，把别名写入记忆层。"""
    desktop = _setup_lnk_dirs(tmp_path, monkeypatch, ["鸣潮"])
    store = MemoryStore(tmp_path / "mem")
    try:
        OpenAppTool(memory=store).execute("鸣潮")
        assert store.get_preference("alias:鸣潮") == str(desktop / "鸣潮.lnk")
    finally:
        store.close()
    assert launched == [str(desktop / "鸣潮.lnk")]


# ------------------------------------------------------------------
# lnk 搜索与多候选选择
# ------------------------------------------------------------------


def test_search_case_insensitive_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    desktop = _setup_lnk_dirs(tmp_path, monkeypatch, ["WeChat", "QQ"])
    result = OpenAppTool().execute("wechat")
    assert launched == [str(desktop / "WeChat.lnk")]
    assert "WeChat" in result


def test_multiple_candidates_picks_closest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    desktop = _setup_lnk_dirs(tmp_path, monkeypatch, ["微信", "微信小程序", "微信支付"])
    result = OpenAppTool().execute("微信")
    # 与「微信」相似度最高的是同名快捷方式
    assert launched == [str(desktop / "微信.lnk")]
    assert "共 3 个候选" in result
    assert "微信" in result


def test_not_found_lists_closest_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[str]
) -> None:
    _setup_lnk_dirs(tmp_path, monkeypatch, ["微信", "钉钉", "QQ音乐", "网易云音乐", "记事本"])
    with pytest.raises(ToolError) as exc_info:
        OpenAppTool().execute("钉钉钉")
    message = str(exc_info.value)
    assert "找不到应用" in message
    assert "钉钉" in message
    assert launched == []


# ------------------------------------------------------------------
# 白名单 auto_allow
# ------------------------------------------------------------------


def test_auto_allow_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = OpenAppTool()
    monkeypatch.setenv("FAIRY_APP_WHITELIST", "微信, 鸣潮 ,WeChat")
    assert tool.auto_allow({"name": "微信"}) is True
    assert tool.auto_allow({"name": "wechat"}) is True  # 大小写不敏感
    assert tool.auto_allow({"name": "QQ"}) is False


def test_auto_allow_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAIRY_APP_WHITELIST", raising=False)
    assert OpenAppTool().auto_allow({"name": "微信"}) is False


# ------------------------------------------------------------------
# 平台与边界
# ------------------------------------------------------------------


def test_non_windows_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ToolError, match="暂未支持该平台"):
        OpenAppTool().execute("微信")


def test_empty_name_raises() -> None:
    with pytest.raises(ToolError, match="不能为空"):
        OpenAppTool().execute("   ")
