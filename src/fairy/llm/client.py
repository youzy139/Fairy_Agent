"""LLM 层：封装 OpenAI 兼容 API 的 chat.completions 调用。"""

from __future__ import annotations

import logging
from typing import Any

from openai import APIConnectionError, APIError, AuthenticationError, OpenAI
from openai.types.chat import ChatCompletionMessage

from fairy.config import Settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败，message 为可读的中文报错。"""


class LLMClient:
    """OpenAI 兼容客户端封装（非流式）。

    支持传入 messages 与 tools schema，返回 ``ChatCompletionMessage`` 对象。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatCompletionMessage:
        """发起一次 chat completion 请求，返回 assistant 的 message 对象。

        网络/鉴权/API 错误统一转换为带中文说明的 :class:`LLMError`。
        部分模型（如 kimi-for-coding）仅允许 ``temperature=1``，
        遇到此类 400 错误时自动去掉 temperature 重试一次。
        """
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": self._settings.temperature,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except APIError as exc:
            if "temperature" in str(exc).lower():
                logger.warning(
                    "模型不接受 temperature=%s，改为不传递该参数重试",
                    kwargs["temperature"],
                )
                kwargs.pop("temperature")
                completion = self._create(kwargs)
            else:
                raise self._convert_error(exc) from exc

        return completion.choices[0].message

    def _create(self, kwargs: dict[str, Any]) -> Any:
        """二次请求封装，错误统一转换。"""
        try:
            return self._client.chat.completions.create(**kwargs)
        except (AuthenticationError, APIConnectionError, APIError) as exc:
            raise self._convert_error(exc) from exc

    def _convert_error(self, exc: Exception) -> LLMError:
        """把 openai SDK 异常转换为带中文说明的 LLMError。"""
        if isinstance(exc, AuthenticationError):
            return LLMError("API 鉴权失败：请检查 OPENAI_API_KEY 是否正确（可在 .env 中配置）。")
        if isinstance(exc, APIConnectionError):
            return LLMError(
                f"无法连接 LLM 服务（{self._settings.openai_base_url}）："
                "请检查网络或 OPENAI_BASE_URL 配置，本地模型请确认服务已启动。"
            )
        return LLMError(f"LLM API 返回错误：{exc}")
