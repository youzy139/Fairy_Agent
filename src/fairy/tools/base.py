"""工具层基类：所有工具的抽象接口与权限级别定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

# 工具权限级别：
# read      只读，自动允许
# write     写入，默认需要确认或白名单
# dangerous 删除、覆盖、提权、批量操作，必须二次确认
# network   网络访问，默认允许但记录日志
# admin     系统级操作，默认禁止
PermissionLevel = Literal["read", "write", "dangerous", "network", "admin"]


class ToolError(Exception):
    """工具执行失败（如路径越权、命令被拒绝），message 为可读的中文说明。"""


class Tool(ABC):
    """工具抽象基类。

    子类必须声明 ``name``、``description``、``parameters``（JSON Schema）
    与 ``permission``，并实现 :meth:`execute`。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    permission: PermissionLevel

    @abstractmethod
    def execute(self, **args: Any) -> str:
        """执行工具并返回文本结果。

        返回值会作为「不可信数据」回传给 LLM；执行失败应抛出 :class:`ToolError`。
        """

    def auto_allow(self, args: dict[str, Any]) -> bool:
        """write 级工具可对白名单内的参数免确认。

        默认返回 ``False``（一律走用户确认流程）。write 级子类可重写本方法，
        对白名单内的参数（如 FAIRY_APP_WHITELIST 中的应用名）返回 ``True``，
        :class:`~fairy.safety.policy.PolicyEngine` 将直接放行而不再询问用户。
        """
        return False

    def to_openai_schema(self) -> dict[str, Any]:
        """生成 OpenAI tools schema 中该工具的条目。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
