"""llm/client.py 单元测试：错误转换与 temperature 回退重试（不发起真实网络请求）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from openai import APIConnectionError, APIError, AuthenticationError

from fairy.config import Settings
from fairy.llm.client import LLMClient, LLMError


def _settings() -> Settings:
    return Settings(openai_api_key="test-key", model="test-model", temperature=0.7)


def _fake_completion(content: str = "你好") -> Any:
    """构造一个形似 ChatCompletion 的桩对象。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _api_error(message: str) -> APIError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return APIError(message, request=request, body=None)


def test_chat_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(_settings())
    monkeypatch.setattr(
        client._client.chat.completions,
        "create",
        lambda **kwargs: _fake_completion(),
    )
    message = client.chat([{"role": "user", "content": "hi"}])
    assert message.content == "你好"


def test_temperature_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型拒绝 temperature 时，应去掉该参数重试一次。"""
    client = LLMClient(_settings())
    calls: list[dict[str, Any]] = []

    def fake_create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise _api_error("invalid temperature: only 1 is allowed for this model")
        return _fake_completion("重试成功")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    message = client.chat([{"role": "user", "content": "hi"}])
    assert message.content == "重试成功"
    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_non_temperature_api_error_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """与 temperature 无关的 API 错误直接抛出，不重试。"""
    client = LLMClient(_settings())
    calls = 0

    def fake_create(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise _api_error("model not found")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMError, match="LLM API 返回错误"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls == 1


def test_auth_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(_settings())

    def fake_create(**kwargs: Any) -> Any:
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        response = httpx.Response(401, request=request)
        raise AuthenticationError("invalid key", response=response, body=None)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMError, match="鉴权失败"):
        client.chat([{"role": "user", "content": "hi"}])


def test_connection_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(_settings())

    def fake_create(**kwargs: Any) -> Any:
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        raise APIConnectionError(request=request)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMError, match="无法连接 LLM 服务"):
        client.chat([{"role": "user", "content": "hi"}])
