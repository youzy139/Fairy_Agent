"""集成测试：Mock LLM 驱动 Agent 主循环，验证调度、审计与最终回复。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from fairy.agent.core import UNTRUSTED_BEGIN, Agent
from fairy.config import Settings
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import CONFIRM_PHRASE, PolicyEngine
from fairy.tools.fs import ListDirTool, ReadFileTool, WriteFileTool
from fairy.tools.registry import ToolRegistry


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[FakeToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        return data


class MockLLM:
    def __init__(self, responses: list[FakeMessage]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.tools_seen: list[Any] = []

    def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> FakeMessage:
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        return self._responses.pop(0)


def _build_agent(workspace: Path, data_dir: Path, llm: MockLLM) -> Agent:
    registry = ToolRegistry()
    registry.register(ListDirTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    policy = PolicyEngine(
        confirm=Mock(return_value=True),
        confirm_phrase=Mock(return_value=CONFIRM_PHRASE),
    )
    return Agent(
        settings=Settings(data_dir=data_dir),
        llm_client=llm,
        registry=registry,
        policy=policy,
        audit=AuditLogger(data_dir),
    )


def test_agent_loop_tool_call_then_text(tmp_path: Path) -> None:
    """LLM 先返回 tool_call 再返回纯文本：验证循环调度、审计写入与最终回复。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    data_dir = tmp_path / "data"

    llm = MockLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("call-1", "list_dir", {"path": "."})]),
            FakeMessage(content="当前目录里有一个文件 a.txt。"),
        ]
    )
    audit = AuditLogger(data_dir)
    agent = _build_agent(workspace, data_dir, llm)

    reply = agent.chat("看看当前目录有什么")

    assert reply == "当前目录里有一个文件 a.txt。"
    assert len(llm.calls) == 2
    # 第一轮请求带上了 tools schema
    assert llm.tools_seen[0] is not None
    tool_names = {t["function"]["name"] for t in llm.tools_seen[0]}
    assert {"list_dir", "read_file", "write_file"} <= tool_names

    # 第二轮 messages 包含 assistant 的 tool_calls 与 tool 结果
    second_call = llm.calls[1]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in second_call)
    tool_msgs = [m for m in second_call if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call-1"
    assert tool_msgs[0]["content"].startswith(UNTRUSTED_BEGIN)
    assert "a.txt" in tool_msgs[0]["content"]

    # 审计日志：一条 allowed=true 的 list_dir 记录
    records = [json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["tool"] == "list_dir"
    assert records[0]["args"] == {"path": "."}
    assert records[0]["allowed"] is True
    assert "a.txt" in records[0]["result_summary"]


def test_agent_denied_call_audited(tmp_path: Path) -> None:
    """被拒绝的工具调用写入审计（allowed=false + deny_reason），结果回传 LLM。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    data_dir = tmp_path / "data"

    llm = MockLLM(
        [
            FakeMessage(
                tool_calls=[FakeToolCall("call-9", "write_file", {"path": "x.txt", "content": "x"})]
            ),
            FakeMessage(content="写入已被取消。"),
        ]
    )
    audit = AuditLogger(data_dir)
    agent = _build_agent(workspace, data_dir, llm)
    agent._policy._confirm = Mock(return_value=False)

    reply = agent.chat("写入 x.txt")

    assert reply == "写入已被取消。"
    assert not (workspace / "x.txt").exists()
    records = [json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["allowed"] is False
    assert "deny_reason" in records[0]


def test_agent_plain_text_reply_no_tools(tmp_path: Path) -> None:
    """LLM 直接返回文本时不写审计。"""
    data_dir = tmp_path / "data"
    llm = MockLLM([FakeMessage(content="你好，我是 Fairy。")])
    audit = AuditLogger(data_dir)
    agent = _build_agent(tmp_path, data_dir, llm)

    assert agent.chat("你好") == "你好，我是 Fairy。"
    assert not audit.path.exists()


def test_agent_max_turns_guard(tmp_path: Path) -> None:
    """LLM 持续返回 tool_call 时，达到最大轮次后强制停止。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    llm = MockLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall(f"call-{i}", "list_dir", {"path": "."})])
            for i in range(20)
        ]
    )
    agent = _build_agent(workspace, data_dir, llm)

    reply = agent.chat("循环吧")

    assert "最大工具调用轮次" in reply
    assert len(llm.calls) == 10


def test_agent_clear_history(tmp_path: Path) -> None:
    llm = MockLLM([FakeMessage(content="ok"), FakeMessage(content="ok2")])
    agent = _build_agent(tmp_path, tmp_path / "data", llm)
    agent.chat("第一条")
    assert len(agent.messages) == 3  # system + user + assistant
    agent.clear_history()
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "system"
