"""apps.py 单元测试：open_project 的项目目录模糊匹配与 VSCode 启动。

code 命令查找与 subprocess.Popen 均 monkeypatch 拦截，不真启动编辑器。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fairy.tools.apps import OpenProjectTool
from fairy.tools.base import ToolError

FAKE_CODE = str(Path("C:/tools/code.cmd"))


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个临时项目根目录，并把 FAIRY_PROJECT_ROOTS 指过去。"""
    root = tmp_path / "project"
    for name in ("kimi-desktop-pet", "fairy", "notes"):
        (root / name).mkdir(parents=True)
    (root / "not-a-dir.txt").write_text("x", encoding="utf-8")  # 文件不参与匹配
    monkeypatch.setenv("FAIRY_PROJECT_ROOTS", str(root))
    return root


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """拦截 subprocess.Popen，返回记录启动参数的列表。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: calls.append(args))
    return calls


@pytest.fixture
def fake_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 shutil.which("code") 返回假路径。"""
    monkeypatch.setattr(shutil, "which", lambda cmd: FAKE_CODE if cmd == "code" else None)


def test_fuzzy_match_and_launch(
    project_root: Path, popen_calls: list[list[str]], fake_code: None
) -> None:
    result = OpenProjectTool().execute("kimi")
    assert popen_calls == [[FAKE_CODE, str(project_root / "kimi-desktop-pet")]]
    assert "kimi-desktop-pet" in result


def test_case_insensitive_match(
    project_root: Path, popen_calls: list[list[str]], fake_code: None
) -> None:
    OpenProjectTool().execute("FAIRY")
    assert popen_calls == [[FAKE_CODE, str(project_root / "fairy")]]


def test_multiple_matches_picks_closest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, popen_calls: list[list[str]], fake_code: None
) -> None:
    root = tmp_path / "project"
    for name in ("pet", "pet-tools", "my-pet-project"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("FAIRY_PROJECT_ROOTS", str(root))
    result = OpenProjectTool().execute("pet")
    assert popen_calls == [[FAKE_CODE, str(root / "pet")]]
    assert "共 3 个匹配" in result


def test_not_found_lists_candidates(
    project_root: Path, popen_calls: list[list[str]], fake_code: None
) -> None:
    with pytest.raises(ToolError) as exc_info:
        OpenProjectTool().execute("桌宠")
    message = str(exc_info.value)
    assert "找不到项目" in message
    assert "kimi-desktop-pet" in message
    assert popen_calls == []


def test_code_fallback_to_localappdata(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    popen_calls: list[list[str]],
) -> None:
    """PATH 中找不到 code 时，回退到 %LOCALAPPDATA% 下的常见安装路径。"""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    fake_bin = tmp_path / "Programs" / "Microsoft VS Code" / "bin"
    fake_bin.mkdir(parents=True)
    code_cmd = fake_bin / "code.cmd"
    code_cmd.write_text("@echo off", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    OpenProjectTool().execute("fairy")
    assert popen_calls == [[str(code_cmd), str(project_root / "fairy")]]


def test_code_not_found_raises(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    popen_calls: list[list[str]],
) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # 空目录，无回退安装
    with pytest.raises(ToolError, match="code"):
        OpenProjectTool().execute("fairy")
    assert popen_calls == []


def test_nonexistent_root_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, popen_calls: list[list[str]], fake_code: None
) -> None:
    root = tmp_path / "project"
    (root / "fairy").mkdir(parents=True)
    monkeypatch.setenv("FAIRY_PROJECT_ROOTS", f"{tmp_path / '不存在'},{root}")
    OpenProjectTool().execute("fairy")
    assert popen_calls == [[FAKE_CODE, str(root / "fairy")]]


def test_auto_allow_always_false() -> None:
    assert OpenProjectTool().auto_allow({"name": "fairy"}) is False


def test_non_windows_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ToolError, match="暂未支持该平台"):
        OpenProjectTool().execute("fairy")
