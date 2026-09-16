"""``fairy init`` 首次运行向导：交互式生成 .env 配置。

面向开源分发场景：新用户不必手写环境变量，按提示选择模型端点预设、
填入 API Key 即可。所有输入函数均可注入，便于测试。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

# 端点预设：(显示名, base_url, 默认模型, 是否需要 API Key, key 提示)
_PRESETS: list[tuple[str, str, str, bool, str]] = [
    (
        "OpenAI 官方",
        "https://api.openai.com/v1",
        "gpt-4o-mini",
        True,
        "sk-...（platform.openai.com 创建）",
    ),
    (
        "Kimi Code（月之暗面）",
        "https://api.kimi.com/coding/v1",
        "kimi-for-coding",
        True,
        "sk-kimi-...（Kimi Code 控制台创建）",
    ),
    (
        "Ollama 本地模型",
        "http://localhost:11434/v1",
        "qwen2.5:7b",
        False,
        "本地模型无需真实 Key",
    ),
    (
        "自定义 OpenAI 兼容端点",
        "",
        "",
        True,
        "你的 API Key",
    ),
]


def run_init(
    env_path: str | os.PathLike[str] = ".env",
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    """运行初始化向导，生成 .env。返回退出码。"""
    path = Path(env_path)

    print_fn("=" * 48)
    print_fn("Fairy 初始化向导")
    print_fn("=" * 48)

    if path.exists():
        answer = input_fn(f"{path} 已存在，覆盖吗？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print_fn("已取消，未修改现有配置。")
            return 1

    print_fn("\n选择模型端点：")
    for i, preset in enumerate(_PRESETS, start=1):
        print_fn(f"  {i}. {preset[0]}")
    choice_raw = input_fn("输入编号 [1]: ").strip() or "1"
    if not choice_raw.isdigit() or not (1 <= int(choice_raw) <= len(_PRESETS)):
        print_fn("无效编号，已取消。")
        return 1
    name, base_url, model, needs_key, key_hint = _PRESETS[int(choice_raw) - 1]

    if not base_url:
        base_url = input_fn("请输入 OpenAI 兼容 Base URL: ").strip()
        if not base_url:
            print_fn("Base URL 不能为空，已取消。")
            return 1
    if not model:
        model = input_fn("请输入模型名: ").strip() or "gpt-4o-mini"

    if needs_key:
        api_key = input_fn(f"请输入 API Key（{key_hint}）: ").strip()
        if not api_key:
            print_fn("API Key 不能为空，已取消。")
            return 1
    else:
        api_key = "ollama"

    content = (
        "# 由 fairy init 生成（本文件已被 .gitignore 忽略，不要提交 Git）\n"
        f"OPENAI_API_KEY={api_key}\n"
        f"OPENAI_BASE_URL={base_url}\n"
        f"FAIRY_MODEL={model}\n"
        "FAIRY_TEMPERATURE=0.7\n"
        "FAIRY_DATA_DIR=~/.fairy\n"
        "FAIRY_LOG_LEVEL=INFO\n"
        "FAIRY_ALLOW_SHELL=false\n"
        "FAIRY_CONFIRM_DANGEROUS=true\n"
        "FAIRY_SANDBOX=false\n"
        "FAIRY_MEMORY_BACKEND=sqlite\n"
    )
    path.write_text(content, encoding="utf-8")

    print_fn(f"\n配置已写入 {path}（端点：{name}）。")
    print_fn("现在可以运行 fairy（CLI）或 fairy --gui（悬浮球）开始使用。")
    print_fn("提示：.env 含密钥，切勿提交 Git。")
    return 0
