"""CLI 入口：``fairy`` 命令与 ``python -m fairy`` 均由此进入。"""

from __future__ import annotations

import argparse
import logging
import sys

from fairy import __version__
from fairy.config import load_settings


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数并启动 CLI。"""
    if sys.platform == "win32":
        # Windows 控制台默认 GBK，统一改为 UTF-8 输出，避免中文乱码
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="fairy",
        description="Fairy Agent：本地优先、权限可控、可审计的桌面 AI 代理。",
    )
    parser.add_argument("--version", action="version", version=f"fairy {__version__}")
    parser.add_argument(
        "--gui",
        action="store_true",
        help='启动桌面悬浮球 GUI（需安装 PySide6：pip install -e ".[gui]"）',
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    if not settings.openai_api_key:
        print(
            "错误：未配置 OPENAI_API_KEY。\n"
            "请复制 .env.example 为 .env 并填入 API Key，或设置环境变量后重试。\n"
            "使用本地模型时，OPENAI_API_KEY 可填任意占位值（如 ollama），"
            "并将 OPENAI_BASE_URL 指向本地兼容端点。",
            file=sys.stderr,
        )
        return 1

    if args.gui:
        try:
            from fairy.ui.floating import run_gui
        except ImportError:
            print(
                '错误：GUI 需要 PySide6。请先安装：pip install -e ".[gui]"',
                file=sys.stderr,
            )
            return 1
        return run_gui(settings)

    from fairy.ui.cli import run_cli

    run_cli(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
