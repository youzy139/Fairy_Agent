"""policy.py 单元测试：各权限级别的决策逻辑。"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from fairy.safety.policy import CONFIRM_PHRASE, PolicyEngine
from fairy.tools.base import PermissionLevel, Tool


class _DummyTool(Tool):
    """仅声明权限级别的假工具。"""

    name = "dummy"
    description = "测试用假工具"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission: PermissionLevel = "read"

    def __init__(self, permission: PermissionLevel) -> None:
        self.permission = permission

    def execute(self, **args: Any) -> str:
        return "ok"


def _engine(confirm_ret: bool = True, phrase_ret: str = CONFIRM_PHRASE, **kwargs: Any):
    confirm = Mock(return_value=confirm_ret)
    phrase = Mock(return_value=phrase_ret)
    engine = PolicyEngine(confirm=confirm, confirm_phrase=phrase, **kwargs)
    return engine, confirm, phrase


def test_read_auto_allowed() -> None:
    engine, confirm, phrase = _engine()
    decision = engine.check(_DummyTool("read"), {})
    assert decision.allowed is True
    confirm.assert_not_called()
    phrase.assert_not_called()


def test_network_allowed_without_confirm() -> None:
    engine, confirm, phrase = _engine()
    decision = engine.check(_DummyTool("network"), {})
    assert decision.allowed is True
    confirm.assert_not_called()
    phrase.assert_not_called()


def test_admin_always_denied() -> None:
    engine, confirm, phrase = _engine()
    decision = engine.check(_DummyTool("admin"), {})
    assert decision.allowed is False
    assert decision.reason is not None
    confirm.assert_not_called()
    phrase.assert_not_called()


def test_write_confirmed() -> None:
    engine, confirm, _ = _engine(confirm_ret=True)
    decision = engine.check(_DummyTool("write"), {"path": "a.txt"})
    assert decision.allowed is True
    confirm.assert_called_once()


def test_write_rejected() -> None:
    engine, confirm, _ = _engine(confirm_ret=False)
    decision = engine.check(_DummyTool("write"), {"path": "a.txt"})
    assert decision.allowed is False
    assert decision.reason is not None
    confirm.assert_called_once()


def test_dangerous_double_confirm_pass() -> None:
    engine, confirm, phrase = _engine(confirm_ret=True, phrase_ret=CONFIRM_PHRASE)
    decision = engine.check(_DummyTool("dangerous"), {"command": "echo hi"})
    assert decision.allowed is True
    confirm.assert_called_once()
    phrase.assert_called_once()


def test_dangerous_first_confirm_rejected() -> None:
    engine, _, phrase = _engine(confirm_ret=False)
    decision = engine.check(_DummyTool("dangerous"), {"command": "echo hi"})
    assert decision.allowed is False
    phrase.assert_not_called()  # 第一次拒绝后不再要求确认词


def test_dangerous_wrong_phrase_rejected() -> None:
    engine, _, phrase = _engine(confirm_ret=True, phrase_ret="随便输入")
    decision = engine.check(_DummyTool("dangerous"), {"command": "echo hi"})
    assert decision.allowed is False
    assert "二次确认" in (decision.reason or "")
    phrase.assert_called_once()


def test_dangerous_phrase_with_whitespace_pass() -> None:
    engine, _, _ = _engine(confirm_ret=True, phrase_ret=f"  {CONFIRM_PHRASE}  \n")
    decision = engine.check(_DummyTool("dangerous"), {})
    assert decision.allowed is True


def test_dangerous_single_confirm_when_disabled() -> None:
    """FAIRY_CONFIRM_DANGEROUS=false 时降级为单次确认。"""
    engine, confirm, phrase = _engine(confirm_ret=True, confirm_dangerous=False)
    decision = engine.check(_DummyTool("dangerous"), {})
    assert decision.allowed is True
    confirm.assert_called_once()
    phrase.assert_not_called()


def test_unknown_level_denied() -> None:
    engine, _, _ = _engine()
    tool = _DummyTool("read")
    tool.permission = "root"  # type: ignore[assignment]
    decision = engine.check(tool, {})
    assert decision.allowed is False
