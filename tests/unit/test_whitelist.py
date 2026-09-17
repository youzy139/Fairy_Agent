"""白名单测试：FAIRY_PATH_WHITELIST（读工具额外目录）与 FAIRY_COMMAND_WHITELIST（命令降级）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.config import load_settings
from fairy.safety.policy import CONFIRM_PHRASE, PolicyEngine
from fairy.tools.base import ToolError
from fairy.tools.fs import ReadFileTool, WriteFileTool
from fairy.tools.shell import RunCommandTool, is_whitelisted

# --- 配置解析 ---


def test_config_whitelists_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAIRY_PATH_WHITELIST", f"{tmp_path}, ~/documents")
    monkeypatch.setenv("FAIRY_COMMAND_WHITELIST", "git status, pwd, ls")
    settings = load_settings()
    assert settings.path_whitelist is not None
    assert settings.path_whitelist[0] == tmp_path
    assert settings.command_whitelist == ["git status", "pwd", "ls"]


def test_config_whitelists_default_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAIRY_PATH_WHITELIST", raising=False)
    monkeypatch.delenv("FAIRY_COMMAND_WHITELIST", raising=False)
    settings = load_settings()
    assert settings.path_whitelist is None
    assert settings.command_whitelist is None


# --- 路径白名单 ---


def test_read_file_allowed_in_extra_root(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    (extra / "note.txt").write_text("外部文件", encoding="utf-8")

    tool = ReadFileTool(workspace, extra_roots=[extra])
    assert tool.execute(str(extra / "note.txt")) == "外部文件"


def test_read_file_still_rejects_non_whitelisted(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")

    tool = ReadFileTool(workspace, extra_roots=[extra])
    with pytest.raises(ToolError, match="路径越权"):
        tool.execute(str(outside))


def test_write_file_ignores_extra_roots(tmp_path: Path) -> None:
    """写工具不吃路径白名单：工作区外永远拒绝。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()

    tool = WriteFileTool(workspace)  # 不接受 extra_roots
    with pytest.raises(ToolError, match="路径越权"):
        tool.execute(str(extra / "evil.txt"), "x")


# --- 命令白名单 ---


def test_is_whitelisted_prefix_match() -> None:
    assert is_whitelisted("git status", ["git status"])
    assert is_whitelisted("git status -s", ["git status"])  # 前缀匹配
    assert not is_whitelisted("git push", ["git status"])


def test_is_whitelisted_all_segments_required() -> None:
    assert is_whitelisted("git status && git status", ["git status"])
    assert not is_whitelisted("git status && rm x", ["git status"])


def test_blacklist_beats_whitelist() -> None:
    assert not is_whitelisted("rm -rf /", ["rm"])


def test_empty_whitelist_never_allows() -> None:
    assert not is_whitelisted("git status", None)
    assert not is_whitelisted("git status", [])


def test_run_command_auto_allow(tmp_path: Path) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=True, command_whitelist=["git status"])
    assert tool.auto_allow({"command": "git status"}) is True
    assert tool.auto_allow({"command": "git push origin main"}) is False


def test_policy_dangerous_whitelisted_single_confirm(tmp_path: Path) -> None:
    """白名单命中的 dangerous 工具：一次确认即可，不再要确认词。"""
    calls: list[str] = []

    def confirm(prompt: str) -> bool:
        calls.append(prompt)
        return True

    def phrase(prompt: str) -> str:
        raise AssertionError("白名单内不应要求确认词")

    policy = PolicyEngine(confirm=confirm, confirm_phrase=phrase, confirm_dangerous=True)
    tool = RunCommandTool(str(tmp_path), allow_shell=True, command_whitelist=["git status"])
    decision = policy.check(tool, {"command": "git status"})
    assert decision.allowed is True
    assert "白名单" in (decision.reason or "")
    assert len(calls) == 1  # 只有一次确认


def test_policy_dangerous_non_whitelisted_still_phrase(tmp_path: Path) -> None:
    """非白名单 dangerous 仍需确认词。"""
    policy = PolicyEngine(
        confirm=lambda p: True,
        confirm_phrase=lambda p: CONFIRM_PHRASE,
        confirm_dangerous=True,
    )
    tool = RunCommandTool(str(tmp_path), allow_shell=True, command_whitelist=["git status"])
    assert policy.check(tool, {"command": "git diff"}).allowed is True  # 走完两次确认才放行
