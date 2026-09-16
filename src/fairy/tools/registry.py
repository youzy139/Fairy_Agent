"""工具注册表：管理工具实例并生成 OpenAI tools schema。"""

from __future__ import annotations

from typing import Any

from fairy.tools.base import Tool


class ToolRegistry:
    """工具注册表，按名称索引工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具；同名工具会被覆盖。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称获取工具，不存在时返回 None。"""
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """返回全部已注册工具。"""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """生成 OpenAI tools schema 列表。"""
        return [tool.to_openai_schema() for tool in self._tools.values()]
