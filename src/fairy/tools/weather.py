"""天气查询工具：get_weather（network 级）。

数据源 wttr.in——免费、无需 API Key、支持中文与城市名/IP 定位。
拉 JSON（format=j1）解析后给出结构化中文摘要；未指定城市时按出口 IP 定位。

注意：wttr.in 是公共服务，偶发限流/不可用属正常，报错而不编造天气。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

_API_URL = "https://wttr.in"
_TIMEOUT_SECONDS = 15
_UA = "fairy-agent/0.1 (weather tool)"


def fetch_weather(city: str) -> dict[str, Any]:
    """请求 wttr.in 并返回 JSON 数据。可注入桩测试。"""
    path = "/" + urllib.parse.quote(city) if city else ""
    url = _API_URL + path + "?" + urllib.parse.urlencode({"format": "j1", "lang": "zh"})
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"天气查询失败：{exc}") from exc


def _first_value(items: list[dict[str, str]] | None) -> str:
    return items[0]["value"] if items else ""


def format_weather(data: dict[str, Any]) -> str:
    """把 wttr.in JSON 格式化为中文摘要。"""
    current = (data.get("current_condition") or [{}])[0]
    if not current:
        raise ToolError("天气数据为空（城市名可能无法识别）。")

    area = (data.get("nearest_area") or [{}])[0]
    location = _first_value(area.get("areaName"))
    country = _first_value(area.get("country"))
    where = f"{location}（{country}）" if location and country else location or "当前位置"

    # 天气描述优先取中文翻译，缺失退回英文原文
    desc = _first_value(current.get("lang_zh")) or _first_value(current.get("weatherDesc"))
    temp = current.get("temp_C", "?")
    feels = current.get("FeelsLikeC", "?")
    humidity = current.get("humidity", "?")
    wind_kmph = current.get("windspeedKmph", "?")

    summary = (
        f"{where}：{desc}，气温 {temp}°C（体感 {feels}°C），湿度 {humidity}%，风速 {wind_kmph}km/h"
    )

    today = (data.get("weather") or [{}])[0]
    low, high = today.get("mintempC"), today.get("maxtempC")
    if low and high:
        summary += f"；今日 {low}~{high}°C。"
    else:
        summary += "。"
    return summary


class WeatherTool(Tool):
    """查询天气实况与当日温度区间（默认按 IP 定位，可指定城市）。"""

    name = "get_weather"
    description = (
        "查询天气实况与当日温度区间。用户问天气/气温/下不下雨时使用；"
        "可指定城市（中英文均可），不指定则按用户当前位置（IP 定位）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名（可选，如 北京、Shanghai）"},
        },
    }
    permission: PermissionLevel = "network"

    def execute(self, city: str = "") -> str:
        return format_weather(fetch_weather(city.strip()))
