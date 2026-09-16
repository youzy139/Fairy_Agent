"""配置加载：从环境变量与 .env 读取 Settings。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 视为 True 的字符串（不区分大小写）
_TRUE_VALUES = {"true", "1", "yes", "on"}


def parse_bool(value: str | None, default: bool = False) -> bool:
    """健壮地解析布尔环境变量。

    支持 ``true/1/yes/on``（大小写不敏感）；空值或 None 返回默认值。
    """
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


@dataclass
class Settings:
    """Fairy 运行时配置，字段与 AGENTS.md「配置约定」表一一对应。"""

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    data_dir: Path = Path("~/.fairy").expanduser()
    log_level: str = "INFO"
    allow_shell: bool = False
    confirm_dangerous: bool = True
    sandbox: bool = False
    memory_backend: str = "sqlite"
    embedding_model: str | None = None


def load_settings(dotenv_path: str | os.PathLike[str] | None = None) -> Settings:
    """加载配置：先读 .env（不覆盖已有环境变量），再从环境变量构建 Settings。

    环境变量优先级高于 .env 文件。``FAIRY_DATA_DIR`` 支持 ``~`` 展开。
    """
    load_dotenv(dotenv_path=dotenv_path, override=False)

    data_dir_raw = os.environ.get("FAIRY_DATA_DIR", "~/.fairy")
    temperature_raw = os.environ.get("FAIRY_TEMPERATURE", "0.7")
    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.7

    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("FAIRY_MODEL", "gpt-4o-mini"),
        temperature=temperature,
        data_dir=Path(data_dir_raw).expanduser(),
        log_level=os.environ.get("FAIRY_LOG_LEVEL", "INFO"),
        allow_shell=parse_bool(os.environ.get("FAIRY_ALLOW_SHELL"), default=False),
        confirm_dangerous=parse_bool(os.environ.get("FAIRY_CONFIRM_DANGEROUS"), default=True),
        sandbox=parse_bool(os.environ.get("FAIRY_SANDBOX"), default=False),
        memory_backend=os.environ.get("FAIRY_MEMORY_BACKEND", "sqlite"),
        embedding_model=os.environ.get("FAIRY_EMBEDDING_MODEL") or None,
    )
