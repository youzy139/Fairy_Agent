"""权限决策引擎：按工具权限级别决定是否允许执行。

- ``read``：自动允许。
- ``write``：需用户确认一次（y/N）。
- ``dangerous``：需二次确认——先 y/N，再输入完整确认词「确认执行」。
  ``confirm_dangerous=False``（对应 FAIRY_CONFIRM_DANGEROUS=false）时降级为单次确认。
- ``network``：允许，但由审计层记录日志。
- ``admin``：一律拒绝。

确认动作通过注入的回调完成：CLI 传入基于 ``input()`` 的实现，测试传 Mock。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fairy.tools.base import Tool

# 危险操作的二次确认词
CONFIRM_PHRASE = "确认执行"

# 确认回调：传入提示语，返回用户是否同意（y/N）
ConfirmCallback = Callable[[str], bool]
# 确认词回调：传入提示语，返回用户输入的原始字符串
PhraseCallback = Callable[[str], str]


@dataclass
class PolicyDecision:
    """一次权限决策的结果。"""

    allowed: bool
    reason: str | None = None


class PolicyEngine:
    """权限决策引擎。"""

    def __init__(
        self,
        confirm: ConfirmCallback,
        confirm_phrase: PhraseCallback,
        confirm_dangerous: bool = True,
    ) -> None:
        self._confirm = confirm
        self._confirm_phrase = confirm_phrase
        self._confirm_dangerous = confirm_dangerous

    def check(self, tool: Tool, args: dict[str, Any]) -> PolicyDecision:
        """对一次工具调用做权限决策。"""
        level = tool.permission

        if level == "read":
            return PolicyDecision(allowed=True)

        if level == "network":
            # 默认允许，审计层负责记录
            return PolicyDecision(allowed=True)

        if level == "admin":
            return PolicyDecision(allowed=False, reason="admin 级系统操作默认禁止，已拒绝。")

        if level == "write":
            prompt = f"工具 {tool.name} 将执行写入操作，参数：{_summarize(args)}。允许吗？[y/N] "
            if self._confirm(prompt):
                return PolicyDecision(allowed=True)
            return PolicyDecision(allowed=False, reason="用户拒绝了写入操作。")

        if level == "dangerous":
            prompt = f"危险操作：工具 {tool.name}，参数：{_summarize(args)}。允许执行吗？[y/N] "
            if not self._confirm(prompt):
                return PolicyDecision(allowed=False, reason="用户拒绝了危险操作。")
            if not self._confirm_dangerous:
                # 配置关闭了二次确认，降级为单次确认（不推荐）
                return PolicyDecision(allowed=True)
            phrase_prompt = f"二次确认：请输入「{CONFIRM_PHRASE}」以继续："
            if self._confirm_phrase(phrase_prompt).strip() == CONFIRM_PHRASE:
                return PolicyDecision(allowed=True)
            return PolicyDecision(allowed=False, reason="二次确认词不正确，已取消执行。")

        # 未知级别按拒绝处理（最小权限原则）
        return PolicyDecision(allowed=False, reason=f"未知权限级别 {level!r}，已拒绝。")


def _summarize(args: dict[str, Any], max_len: int = 200) -> str:
    """生成参数的简短摘要，避免把超长内容刷到终端。"""
    text = repr(args)
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text
