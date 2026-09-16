"""Shell 工具：run_command（dangerous 级）。

- ``FAIRY_ALLOW_SHELL=false`` 时直接拒绝执行。
- 内置命令黑名单；命令先按 ``;``、``&&``、``||``、``|`` 拆分，
  每个片段单独做黑名单检查，防止拼接绕过。
- 使用 subprocess 执行，捕获 stdout/stderr，默认超时 30 秒。
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

DEFAULT_TIMEOUT_SECONDS = 30

# 黑名单关键词（小写匹配）：高危删除、格式化、提权、关机等
_BLACKLIST = (
    "rm -rf /",
    "rm -rf ~",
    "rm -rf *",
    "rm -fr /",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    ":(){",
    "format c:",
    "format d:",
    "del /f /s /q",
    "rd /s /q c:\\",
    "diskpart",
    "sudo ",
    "chmod -r 777 /",
    "chown -r",
)

# 命令拆分符：分号、&&、||、管道
_SPLIT_PATTERN = re.compile(r";|&&|\|\||\|")


def _segments(command: str) -> list[str]:
    """把命令按拼接符拆成片段，便于逐段黑名单检查。"""
    parts = _SPLIT_PATTERN.split(command)
    return [p.strip().lower() for p in parts if p.strip()]


def is_blacklisted(command: str) -> str | None:
    """检查命令（含拼接片段）是否命中黑名单，命中返回原因，否则返回 None。"""
    lowered = command.strip().lower()
    checks = [lowered, *_segments(command)]
    for segment in checks:
        for bad in _BLACKLIST:
            if bad in segment:
                return f"命令命中黑名单关键词 {bad!r}"
        # 兜底：片段的首个 token 是 mkfs.* / format 等危险程序
        try:
            tokens = shlex.split(segment, posix=False)
        except ValueError:
            tokens = segment.split()
        if tokens:
            head = tokens[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            if head.startswith("mkfs") or head in {"format", "shutdown", "reboot", "diskpart"}:
                return f"命令调用了危险程序 {head!r}"
    return None


class RunCommandTool(Tool):
    """执行 Shell 命令（dangerous 级，默认关闭并需二次确认）。"""

    name = "run_command"
    description = "在工作区执行一条 Shell 命令（危险操作，默认关闭，需要二次确认）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 Shell 命令"},
            "timeout": {
                "type": "integer",
                "description": f"超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS}",
            },
        },
        "required": ["command"],
    }
    permission: PermissionLevel = "dangerous"

    def __init__(self, workspace: str, allow_shell: bool = False) -> None:
        self._workspace = workspace
        self._allow_shell = allow_shell

    def execute(self, command: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
        if not self._allow_shell:
            raise ToolError("Shell 工具已禁用：请在配置中设置 FAIRY_ALLOW_SHELL=true 后重试。")

        reason = is_blacklisted(command)
        if reason is not None:
            raise ToolError(f"命令被拒绝：{reason}。")

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self._workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"命令执行超时（{timeout} 秒），已终止。") from exc

        output = (proc.stdout or "") + (proc.stderr or "")
        output = output.strip()
        if not output:
            output = "（无输出）"
        return f"退出码 {proc.returncode}\n{output}"
