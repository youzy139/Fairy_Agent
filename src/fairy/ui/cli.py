"""CLI 交互界面：REPL、确认回调、工具调用摘要展示。"""

from __future__ import annotations

import json
import os
from typing import Any

from fairy.agent.core import Agent
from fairy.config import Settings
from fairy.memory.store import MemoryStore
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import PolicyEngine
from fairy.tools.desktop import OrganizeDesktopTool
from fairy.tools.fs import ListDirTool, ReadFileTool, WriteFileTool
from fairy.tools.registry import ToolRegistry
from fairy.tools.screenshot import ScreenshotTool
from fairy.tools.search import SearchFilesTool
from fairy.tools.shell import RunCommandTool

HELP_TEXT = """\
可用命令：
  /help          显示本帮助
  /new           开启新会话
  /sessions      列出最近 10 个会话（id、时间、标题）
  /resume <id>   恢复指定 id 的会话
  /clear         清空对话历史
  /exit          退出（等价于 /quit）
  /quit          退出
其他输入将作为消息发送给 Fairy。\
"""


def cli_confirm(prompt: str) -> bool:
    """write/dangerous 级的一次确认回调（y/N，默认否）。"""
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def cli_confirm_phrase(prompt: str) -> str:
    """dangerous 级的二次确认词回调，原样返回用户输入。"""
    return input(prompt)


def build_registry(
    settings: Settings,
    workspace: str,
    memory: Any = None,
    knowledge: Any = None,
) -> ToolRegistry:
    """按当前配置构建工具注册表，工作区根目录默认为当前目录。

    ``memory`` 为 MemoryStore 时，别名/收藏类工具（open_app、browser_open、
    remember）启用对话教学记忆。``knowledge`` 为 KnowledgeBase 时知识库工具
    直接复用该实例；未传入则按配置自建（嵌入模型懒加载，无额外开销）。
    """
    # 局部导入：桌面/窗口/应用类工具为 Windows 专属，保持模块在非 Windows 可导入
    from fairy.tools.apps import OpenAppTool, OpenProjectTool
    from fairy.tools.browser import BrowserOpenTool
    from fairy.tools.clipboard import ClipboardReadTool, ClipboardWriteTool
    from fairy.tools.desktop_icons import ArrangeDesktopTool, ListDesktopIconsTool
    from fairy.tools.knowledge import (
        KnowledgeAddTool,
        KnowledgeForgetTool,
        KnowledgeListTool,
        KnowledgeSearchTool,
        build_knowledge,
    )
    from fairy.tools.remember import RememberTool
    from fairy.tools.weather import WeatherTool
    from fairy.tools.websearch import WebSearchTool
    from fairy.tools.windows_mgmt import (
        ActivateWindowTool,
        ListWindowsTool,
        MinimizeAllWindowsTool,
    )

    if knowledge is None:
        knowledge = build_knowledge(settings)

    registry = ToolRegistry()
    extra = settings.path_whitelist
    registry.register(ListDirTool(workspace, extra_roots=extra))
    registry.register(ReadFileTool(workspace, extra_roots=extra))
    registry.register(WriteFileTool(workspace))
    registry.register(SearchFilesTool(workspace, extra_roots=extra))
    registry.register(
        RunCommandTool(
            workspace,
            allow_shell=settings.allow_shell,
            command_whitelist=settings.command_whitelist,
        )
    )
    registry.register(ScreenshotTool(workspace))
    registry.register(OrganizeDesktopTool())
    registry.register(ListDesktopIconsTool())
    registry.register(ArrangeDesktopTool())
    registry.register(OpenAppTool(memory))
    registry.register(OpenProjectTool())
    registry.register(ClipboardReadTool())
    registry.register(ClipboardWriteTool())
    registry.register(BrowserOpenTool(memory))
    registry.register(ListWindowsTool())
    registry.register(ActivateWindowTool())
    registry.register(MinimizeAllWindowsTool())
    registry.register(RememberTool(memory))
    registry.register(WebSearchTool())
    registry.register(WeatherTool())
    registry.register(KnowledgeAddTool(workspace, knowledge, extra_roots=extra))
    registry.register(KnowledgeSearchTool(knowledge))
    registry.register(KnowledgeListTool(knowledge))
    registry.register(KnowledgeForgetTool(knowledge))
    return registry


def build_agent(
    settings: Settings,
    workspace: str | None = None,
    memory: MemoryStore | None = None,
    session_id: int | None = None,
) -> Agent:
    """组装 Agent 及其依赖（LLM 客户端由调用方注入时另行构造）。"""
    from fairy.llm.client import LLMClient

    ws = workspace or os.getcwd()
    from fairy.tools.knowledge import build_knowledge

    knowledge = build_knowledge(settings)
    registry = build_registry(settings, ws, memory=memory, knowledge=knowledge)
    policy = PolicyEngine(
        confirm=cli_confirm,
        confirm_phrase=cli_confirm_phrase,
        confirm_dangerous=settings.confirm_dangerous,
    )
    audit = AuditLogger(settings.data_dir)
    return Agent(
        settings=settings,
        llm_client=LLMClient(settings),
        registry=registry,
        policy=policy,
        audit=audit,
        on_tool_call=_print_tool_call,
        memory=memory,
        session_id=session_id,
        knowledge=knowledge,
    )


def _print_tool_call(name: str, args: dict[str, Any]) -> None:
    """工具调用摘要：只打印工具名与截断后的参数，避免刷屏。"""
    summary = json.dumps(args, ensure_ascii=False)
    if len(summary) > 200:
        summary = summary[:200] + "..."
    print(f"[工具调用] {name} {summary}")


def run_cli(settings: Settings) -> None:
    """启动 CLI REPL。"""
    workspace = os.getcwd()
    memory: MemoryStore | None = None
    session_id: int | None = None
    if settings.memory_backend == "sqlite":
        memory = MemoryStore(settings.data_dir)
        session_id = memory.create_session(workspace)
    agent = build_agent(settings, workspace=workspace, memory=memory, session_id=session_id)
    # 会话标题延迟到首条用户消息时设置（取前 20 字）
    title_pending = memory is not None

    print("=" * 48)
    print("Fairy Agent v0.1 —— 本地优先的 AI 副驾驶")
    print(f"当前模型：{settings.model}（{settings.openai_base_url}）")
    print(f"Shell 工具：{'已启用（危险，需二次确认）' if settings.allow_shell else '已禁用'}")
    print(f"记忆后端：{settings.memory_backend}" + (f"（会话 id={session_id}）" if memory else ""))
    print("输入 /help 查看命令，/exit 退出。")
    print("=" * 48)

    try:
        while True:
            try:
                user_text = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break

            if not user_text:
                continue
            if user_text in {"/exit", "/quit"}:
                print("再见。")
                break
            if user_text == "/help":
                print(HELP_TEXT)
                continue
            if user_text == "/clear":
                agent.clear_history()
                print("对话历史已清空。")
                continue
            if user_text == "/new":
                if memory is None:
                    print("记忆后端未启用，无法创建新会话。")
                    continue
                session_id = memory.create_session(workspace)
                agent = build_agent(
                    settings, workspace=workspace, memory=memory, session_id=session_id
                )
                title_pending = True
                print(f"已开启新会话（id={session_id}）。")
                continue
            if user_text == "/sessions":
                if memory is None:
                    print("记忆后端未启用，无历史会话。")
                    continue
                sessions = memory.list_sessions(limit=10)
                if not sessions:
                    print("暂无历史会话。")
                else:
                    for s in sessions:
                        title = s["title"] or "(无标题)"
                        print(f"  {s['id']:>4}  {s['created_at']}  {title}")
                continue
            if user_text.startswith("/resume"):
                if memory is None:
                    print("记忆后端未启用，无法恢复会话。")
                    continue
                parts = user_text.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    print("用法：/resume <会话 id>")
                    continue
                target = int(parts[1])
                try:
                    agent.load_session(target)
                except Exception as exc:
                    print(f"恢复会话失败：{exc}")
                    continue
                session_id = target
                title_pending = False
                print(f"已恢复会话 {target}（共 {len(agent.messages)} 条消息）。")
                continue

            try:
                reply = agent.chat(user_text)
            except Exception as exc:  # LLM 错误等，保持 REPL 不退出
                print(f"Fairy: 出错了——{exc}")
                continue
            if title_pending and memory is not None and session_id is not None:
                memory.set_session_title(session_id, user_text[:20])
                title_pending = False
            print(f"Fairy: {reply}")
    finally:
        if memory is not None:
            memory.close()
