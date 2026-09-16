"""集成测试：Agent 挂载 MemoryStore 后的消息持久化与跨实例会话恢复。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from fairy.agent.core import Agent
from fairy.config import Settings
from fairy.memory.store import MemoryStore
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import CONFIRM_PHRASE, PolicyEngine
from fairy.tools.fs import ListDirTool
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

    def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> FakeMessage:
        return self._responses.pop(0)


def _build_agent(
    workspace: Path,
    data_dir: Path,
    llm: MockLLM,
    memory: MemoryStore | None = None,
    session_id: int | None = None,
) -> Agent:
    registry = ToolRegistry()
    registry.register(ListDirTool(workspace))
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
        memory=memory,
        session_id=session_id,
    )


def test_agent_persists_and_resume_restores(tmp_path: Path) -> None:
    """聊两轮（含一次工具调用）后，新建 Agent + load_session 恢复出一致的消息序列。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    data_dir = tmp_path / "data"

    memory = MemoryStore(data_dir)
    session_id = memory.create_session(str(workspace), title="测试会话")

    llm = MockLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("call-1", "list_dir", {"path": "."})]),
            FakeMessage(content="目录里有 a.txt。"),
            FakeMessage(content="不客气。"),
        ]
    )
    agent = _build_agent(workspace, data_dir, llm, memory=memory, session_id=session_id)

    agent.chat("看看目录")
    agent.chat("谢谢")

    # 持久化的消息与内存中的历史完全一致
    persisted = memory.get_messages(session_id)
    assert persisted == agent.messages
    roles = [m["role"] for m in persisted]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user", "assistant"]
    # tool_calls 字段完整保留
    assert persisted[2]["tool_calls"][0]["function"]["name"] == "list_dir"
    assert persisted[3]["tool_call_id"] == "call-1"

    # 新建 Agent 并恢复会话：消息序列一致
    agent2 = _build_agent(workspace, data_dir, MockLLM([]), memory=memory, session_id=None)
    agent2.load_session(session_id)
    assert agent2.messages == agent.messages

    # 恢复后继续对话，新消息追加到同一会话
    agent2._llm = MockLLM([FakeMessage(content="继续。")])
    agent2.chat("继续聊")
    assert memory.get_messages(session_id) == agent2.messages
    assert len(agent2.messages) == len(agent.messages) + 2
    memory.close()


def test_agent_resume_across_store_reopen(tmp_path: Path) -> None:
    """数据库重开后仍能恢复会话（system 首条直接使用，不重复追加）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    data_dir = tmp_path / "data"

    memory = MemoryStore(data_dir)
    session_id = memory.create_session(str(workspace))
    agent = _build_agent(
        workspace,
        data_dir,
        MockLLM([FakeMessage(content="你好。")]),
        memory=memory,
        session_id=session_id,
    )
    agent.chat("你好")
    expected = list(agent.messages)
    memory.close()

    memory2 = MemoryStore(data_dir)
    agent2 = _build_agent(workspace, data_dir, MockLLM([]), memory=memory2, session_id=session_id)
    # 会话已有内容，构造时不重复写 system
    assert memory2.get_messages(session_id) == expected
    agent2.load_session(session_id)
    assert agent2.messages == expected
    memory2.close()


def test_load_session_prepends_system_when_missing(tmp_path: Path) -> None:
    """库中历史缺少 system 首条时，恢复后当前 system prompt 保留在最前。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    data_dir = tmp_path / "data"

    memory = MemoryStore(data_dir)
    session_id = memory.create_session(str(workspace))
    # 模拟一条没有 system 的历史（如旧版本数据）
    memory.append_message(session_id, {"role": "user", "content": "旧消息"})

    agent = _build_agent(workspace, data_dir, MockLLM([]), memory=memory)
    agent.load_session(session_id)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1] == {"role": "user", "content": "旧消息"}
    memory.close()


def test_load_session_without_memory_raises(tmp_path: Path) -> None:
    """未挂载记忆层时 load_session 抛出明确错误。"""
    agent = _build_agent(tmp_path, tmp_path / "data", MockLLM([]))
    with pytest.raises(RuntimeError, match="MemoryStore"):
        agent.load_session(1)


def test_agent_without_memory_unchanged(tmp_path: Path) -> None:
    """memory=None 时不创建数据库文件，行为与原有一致。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    agent = _build_agent(workspace, data_dir, MockLLM([FakeMessage(content="ok")]))
    assert agent.chat("hi") == "ok"
    assert not (data_dir / "memory.db").exists()
