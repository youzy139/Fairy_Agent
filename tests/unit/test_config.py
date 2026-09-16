"""config.py 单元测试：默认值、环境变量覆盖、布尔解析。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fairy.config import load_settings, parse_bool

_ALL_ENV_VARS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "FAIRY_MODEL",
    "FAIRY_TEMPERATURE",
    "FAIRY_DATA_DIR",
    "FAIRY_LOG_LEVEL",
    "FAIRY_ALLOW_SHELL",
    "FAIRY_CONFIRM_DANGEROUS",
    "FAIRY_SANDBOX",
    "FAIRY_MEMORY_BACKEND",
    "FAIRY_EMBEDDING_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清空相关环境变量，并阻止 load_dotenv 读取仓库里的真实 .env。"""
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _load(tmp_path: Path):
    """用一个不存在的 dotenv 路径加载配置，避免读取真实 .env。"""
    return load_settings(dotenv_path=tmp_path / "no-such.env")


def test_defaults(tmp_path: Path) -> None:
    settings = _load(tmp_path)
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-4o-mini"
    assert settings.temperature == pytest.approx(0.7)
    assert settings.data_dir == Path("~/.fairy").expanduser()
    assert settings.log_level == "INFO"
    assert settings.allow_shell is False
    assert settings.confirm_dangerous is True
    assert settings.sandbox is False
    assert settings.memory_backend == "sqlite"
    assert settings.embedding_model is None


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("FAIRY_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("FAIRY_TEMPERATURE", "0.1")
    monkeypatch.setenv("FAIRY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("FAIRY_MEMORY_BACKEND", "memory")
    monkeypatch.setenv("FAIRY_EMBEDDING_MODEL", "bge-m3")

    settings = _load(tmp_path)
    assert settings.openai_api_key == "sk-test"
    assert settings.openai_base_url == "http://localhost:11434/v1"
    assert settings.model == "qwen2.5:7b"
    assert settings.temperature == pytest.approx(0.1)
    assert settings.log_level == "DEBUG"
    assert settings.memory_backend == "memory"
    assert settings.embedding_model == "bge-m3"


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES", "on", "On"])
def test_parse_bool_true_values(value: str) -> None:
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "garbage", "2"])
def test_parse_bool_false_values(value: str) -> None:
    assert parse_bool(value) is False


def test_parse_bool_default() -> None:
    assert parse_bool(None, default=True) is True
    assert parse_bool(None, default=False) is False


def test_bool_env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAIRY_ALLOW_SHELL", "yes")
    monkeypatch.setenv("FAIRY_CONFIRM_DANGEROUS", "0")
    monkeypatch.setenv("FAIRY_SANDBOX", "ON")
    settings = _load(tmp_path)
    assert settings.allow_shell is True
    assert settings.confirm_dangerous is False
    assert settings.sandbox is True


def test_data_dir_tilde_expansion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAIRY_DATA_DIR", "~/fairy-test-data")
    settings = _load(tmp_path)
    assert settings.data_dir == Path("~/fairy-test-data").expanduser()
    assert "~" not in str(settings.data_dir)


def test_invalid_temperature_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAIRY_TEMPERATURE", "not-a-number")
    assert _load(tmp_path).temperature == pytest.approx(0.7)


def test_dotenv_file_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式指定的 .env 文件会被读取，但不覆盖已有环境变量。"""
    dotenv = tmp_path / "custom.env"
    dotenv.write_text("FAIRY_MODEL=from-dotenv\nFAIRY_ALLOW_SHELL=true\n", encoding="utf-8")
    monkeypatch.setenv("FAIRY_MODEL", "from-env")
    settings = load_settings(dotenv_path=dotenv)
    assert settings.model == "from-env"  # 环境变量优先
    assert settings.allow_shell is True  # 来自 .env


def test_no_real_dotenv_loaded(tmp_path: Path) -> None:
    """确认测试环境不会意外读到仓库根目录的真实 .env。"""
    settings = _load(tmp_path)
    assert settings.openai_api_key is None or settings.openai_api_key == os.environ.get(
        "OPENAI_API_KEY"
    )
