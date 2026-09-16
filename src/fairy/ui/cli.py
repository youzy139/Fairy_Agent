"""CLI 交互界面：REPL、确认回调、工具调用摘要展示。"""

from __future__ import annotations

import json
import os
from typing import Any

from fairy.agent.core import Agent
from fairy.config import Settings
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import PolicyEngine
from fairy.tools.fs import ListDirTool, ReadFileTool, WriteFileTool
from fairy.tools.registry import ToolRegistry
from fairy.tools.search import SearchFilesTool
from fairy.tools.shell import RunCommandTool

HELP_TEXT = """\
可用命令：
  /help   显示本帮助
  /clear  清空对话历史
  /exit   退出（等价于 /quit）
  /quit   退出
其他输入将作为消息发送给 Fairy。\
"""


def cli_confirm(prompt: str) -> bool:
    """write/dangerous 级的一次确认回调（y/N，默认否）。"""
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def cli_confirm_phrase(prompt: str) -> str:
    """dangerous 级的二次确认词回调，原样返回用户输入。"""
    return input(prompt)


def build_registry(settings: Settings, workspace: str) -> ToolRegistry:
    """按当前配置构建工具注册表，工作区根目录默认为当前目录。"""
    registry = ToolRegistry()
    registry.register(ListDirTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(RunCommandTool(workspace, allow_shell=settings.allow_shell))
    return registry


def build_agent(settings: Settings, workspace: str | None = None) -> Agent:
    """组装 Agent 及其依赖（LLM 客户端由调用方注入时另行构造）。"""
    from fairy.llm.client import LLMClient

    ws = workspace or os.getcwd()
    registry = build_registry(settings, ws)
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
    )


def _print_tool_call(name: str, args: dict[str, Any]) -> None:
    """工具调用摘要：只打印工具名与截断后的参数，避免刷屏。"""
    summary = json.dumps(args, ensure_ascii=False)
    if len(summary) > 200:
        summary = summary[:200] + "..."
    print(f"[工具调用] {name} {summary}")


def run_cli(settings: Settings) -> None:
    """启动 CLI REPL。"""
    agent = build_agent(settings)

    print("=" * 48)
    print("Fairy Agent v0.1 —— 本地优先的 AI 副驾驶")
    print(f"当前模型：{settings.model}（{settings.openai_base_url}）")
    print(f"Shell 工具：{'已启用（危险，需二次确认）' if settings.allow_shell else '已禁用'}")
    print("输入 /help 查看命令，/exit 退出。")
    print("=" * 48)

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

        try:
            reply = agent.chat(user_text)
        except Exception as exc:  # LLM 错误等，保持 REPL 不退出
            print(f"Fairy: 出错了——{exc}")
            continue
        print(f"Fairy: {reply}")
