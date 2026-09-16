"""文件工具：list_dir / read_file / write_file。

安全要求：所有路径经 resolve（含符号链接解析）后必须位于工作区根目录内，
越界路径（``../..``、绝对路径、符号链接逃逸）一律抛出 :class:`ToolError`。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

# read_file 单次最大读取字节数
MAX_READ_BYTES = 256 * 1024


def _resolve_in_workspace(workspace: Path, raw_path: str) -> Path:
    """将用户路径解析为工作区内的绝对路径。

    ``Path.resolve()`` 会同时展开 ``..`` 与符号链接，因此符号链接逃逸
    也会在 ``is_relative_to`` 检查中被拒绝。
    """
    workspace_resolved = workspace.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace_resolved / candidate
    resolved = candidate.resolve()
    if not (resolved == workspace_resolved or workspace_resolved in resolved.parents):
        raise ToolError(f"路径越权：{raw_path!r} 不在工作区 {workspace_resolved} 内，已拒绝访问。")
    return resolved


class ListDirTool(Tool):
    """列出工作区内指定目录的内容。"""

    name = "list_dir"
    description = "列出指定目录下的文件与子目录（仅限工作区内）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径，相对于工作区根目录，默认为根目录本身",
            }
        },
        "required": [],
    }
    permission: PermissionLevel = "read"

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self._workspace = Path(workspace)

    def execute(self, path: str = ".") -> str:
        target = _resolve_in_workspace(self._workspace, path)
        if not target.exists():
            raise ToolError(f"目录不存在：{path!r}")
        if not target.is_dir():
            raise ToolError(f"不是目录：{path!r}")
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        if not entries:
            return "（空目录）"
        lines = [f"{'[目录]' if e.is_dir() else '[文件]'} {e.name}" for e in entries]
        return "\n".join(lines)


class ReadFileTool(Tool):
    """读取工作区内文件内容（最大 256KB）。"""

    name = "read_file"
    description = "读取指定文件的文本内容（仅限工作区内，最大 256KB）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "文件路径，相对于工作区根目录"}},
        "required": ["path"],
    }
    permission: PermissionLevel = "read"

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self._workspace = Path(workspace)

    def execute(self, path: str) -> str:
        target = _resolve_in_workspace(self._workspace, path)
        if not target.exists():
            raise ToolError(f"文件不存在：{path!r}")
        if not target.is_file():
            raise ToolError(f"不是文件：{path!r}")
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(
                f"文件过大：{size} 字节，超过单次读取上限 {MAX_READ_BYTES} 字节（256KB）。"
            )
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是有效的 UTF-8 文本：{path!r}") from exc


class WriteFileTool(Tool):
    """写入工作区内文件（write 级权限，覆盖已存在文件同样需确认）。"""

    name = "write_file"
    description = "将文本内容写入指定文件（仅限工作区内；覆盖已存在文件时同样需确认）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对于工作区根目录"},
            "content": {"type": "string", "description": "要写入的文本内容"},
        },
        "required": ["path", "content"],
    }
    permission: PermissionLevel = "write"

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self._workspace = Path(workspace)

    def execute(self, path: str, content: str) -> str:
        target = _resolve_in_workspace(self._workspace, path)
        existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            # 如 Windows 不允许名为 "...." 的目录等，统一转为 ToolError
            raise ToolError(f"写入失败：{path!r}（{exc}）") from exc
        action = "覆盖" if existed else "创建"
        return f"已{action}文件：{target}（{len(content)} 字符）"
