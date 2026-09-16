"""shell.py 单元测试：开关、黑名单与正常执行。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fairy.tools.base import ToolError
from fairy.tools.shell import RunCommandTool, is_blacklisted


def test_shell_disabled_by_default(tmp_path: Path) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=False)
    with pytest.raises(ToolError, match="已禁用"):
        tool.execute(command="echo hello")


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "RM -RF /",  # 大小写不敏感
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown /s /t 0",
        "reboot",
        "diskpart",
    ],
)
def test_blacklist_direct(tmp_path: Path, command: str) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=True)
    with pytest.raises(ToolError, match="命令被拒绝"):
        tool.execute(command=command)


@pytest.mark.parametrize(
    "command",
    [
        "echo ok; rm -rf /",  # 分号拼接
        "echo ok && shutdown /s",  # && 拼接
        "echo ok || mkfs.ext4 /dev/sda",  # || 拼接
        "cat file | dd if=/dev/zero of=/dev/sda",  # 管道拼接
    ],
)
def test_blacklist_chained_commands(command: str) -> None:
    """拼接出的黑名单命令也必须被识别。"""
    assert is_blacklisted(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        "dir",
        "python --version",
    ],
)
def test_normal_commands_not_blacklisted(command: str) -> None:
    assert is_blacklisted(command) is None


def test_echo_runs(tmp_path: Path) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=True)
    result = tool.execute(command="echo hello")
    assert "退出码 0" in result
    assert "hello" in result


def test_nonzero_exit_code_captured(tmp_path: Path) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=True)
    result = tool.execute(command="exit 3")
    assert "退出码 3" in result


def test_timeout(tmp_path: Path) -> None:
    tool = RunCommandTool(str(tmp_path), allow_shell=True)
    with pytest.raises(ToolError, match="超时"):
        tool.execute(command='python -c "import time; time.sleep(30)"', timeout=1)
