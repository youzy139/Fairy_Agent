"""搜索工具：search_files（read 级）。

在工作区内按文件名 glob 模式搜索，可选按文件内容关键词过滤。
路径限制与 fs.py 一致：任何匹配结果都必须位于工作区根目录内。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError
from fairy.tools.fs import MAX_READ_BYTES, _resolve_in_workspace

# 单次搜索最多返回的匹配条数，防止结果刷屏
MAX_RESULTS = 100


class SearchFilesTool(Tool):
    """在工作区内搜索文件：按文件名 glob 模式，可选内容关键词。"""

    name = "search_files"
    description = (
        "在工作区内搜索文件：pattern 为文件名 glob 模式（如 *.py、**/*.md），"
        "可选 keyword 按文件内容关键词过滤（仅检查 256KB 以内的文本文件）"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "文件名 glob 模式，如 *.py 或 **/*.md",
            },
            "keyword": {
                "type": "string",
                "description": "可选：文件内容必须包含的关键词",
            },
            "path": {
                "type": "string",
                "description": "搜索起始目录，相对于工作区根目录，默认为根目录",
            },
        },
        "required": ["pattern"],
    }
    permission: PermissionLevel = "read"

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        extra_roots: list[Path] | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._extra_roots = extra_roots or []

    def execute(self, pattern: str, keyword: str | None = None, path: str = ".") -> str:
        start = _resolve_in_workspace(self._workspace, path, self._extra_roots)
        if not start.exists():
            raise ToolError(f"目录不存在：{path!r}")
        if not start.is_dir():
            raise ToolError(f"不是目录：{path!r}")
        if not pattern.strip():
            raise ToolError("搜索模式不能为空。")

        matches: list[str] = []
        try:
            candidates = start.glob(pattern)
            for candidate in candidates:
                if len(matches) >= MAX_RESULTS:
                    break
                if not candidate.is_file():
                    continue
                # glob 不会逃出 start，但符号链接可能指向允许范围外，逐一校验
                resolved = candidate.resolve()
                workspace_resolved = self._workspace.resolve()
                allowed_roots = [
                    workspace_resolved,
                    *(root.resolve() for root in self._extra_roots),
                ]
                if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                    continue
                if keyword is not None and not _contains_keyword(resolved, keyword):
                    continue
                # 白名单目录内的结果用绝对路径展示（无法相对工作区表示）
                try:
                    display = str(resolved.relative_to(workspace_resolved))
                except ValueError:
                    display = str(resolved)
                matches.append(display)
        except (OSError, ValueError) as exc:
            raise ToolError(f"搜索失败：{exc}") from exc

        if not matches:
            return "未找到匹配的文件。"
        header = f"共找到 {len(matches)} 个匹配文件"
        if len(matches) >= MAX_RESULTS:
            header += f"（已达上限 {MAX_RESULTS}，结果可能被截断）"
        return header + "：\n" + "\n".join(sorted(matches))


def _contains_keyword(path: Path, keyword: str) -> bool:
    """检查文本文件内容是否包含关键词；过大或非 UTF-8 文件直接跳过。"""
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return False
        return keyword in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
