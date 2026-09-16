"""onboarding.py 单元测试：fairy init 向导的分支与 .env 生成。"""

from __future__ import annotations

from pathlib import Path

from fairy.onboarding import run_init


def _run(tmp_path: Path, answers: list[str]) -> tuple[int, list[str], Path]:
    """用答案队列驱动向导，返回 (退出码, 输出, env 路径)。"""
    printed: list[str] = []
    queue = iter(answers)
    env_path = tmp_path / ".env"
    code = run_init(env_path, input_fn=lambda prompt: next(queue), print_fn=printed.append)
    return code, printed, env_path


def test_init_kimi_preset(tmp_path: Path) -> None:
    code, printed, env = _run(tmp_path, ["2", "sk-kimi-test"])
    assert code == 0
    content = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-kimi-test" in content
    assert "OPENAI_BASE_URL=https://api.kimi.com/coding/v1" in content
    assert "FAIRY_MODEL=kimi-for-coding" in content


def test_init_openai_preset_default_choice(tmp_path: Path) -> None:
    code, _, env = _run(tmp_path, ["", "sk-test-key"])
    assert code == 0
    assert "https://api.openai.com/v1" in env.read_text(encoding="utf-8")


def test_init_ollama_no_key_needed(tmp_path: Path) -> None:
    code, _, env = _run(tmp_path, ["3"])
    assert code == 0
    content = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=ollama" in content
    assert "localhost:11434" in content


def test_init_custom_endpoint(tmp_path: Path) -> None:
    code, _, env = _run(tmp_path, ["4", "http://192.168.1.10:8000/v1", "my-model", "sk-x"])
    assert code == 0
    content = env.read_text(encoding="utf-8")
    assert "http://192.168.1.10:8000/v1" in content
    assert "my-model" in content


def test_init_existing_env_requires_confirm(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=1", encoding="utf-8")
    code, printed, _ = _run(tmp_path, ["n"])
    assert code == 1
    assert env.read_text(encoding="utf-8") == "EXISTING=1"  # 未被覆盖


def test_init_invalid_choice(tmp_path: Path) -> None:
    code, _, env = _run(tmp_path, ["9"])
    assert code == 1
    assert not env.exists()


def test_init_empty_key_rejected(tmp_path: Path) -> None:
    code, _, env = _run(tmp_path, ["1", ""])
    assert code == 1
    assert not env.exists()
