"""weather.py 单元测试：wttr.in JSON 解析与格式化（网络请求全部打桩）。"""

from __future__ import annotations

import pytest

from fairy.tools.base import ToolError
from fairy.tools.weather import WeatherTool, fetch_weather, format_weather

_SAMPLE = {
    "current_condition": [
        {
            "temp_C": "26",
            "FeelsLikeC": "28",
            "humidity": "65",
            "windspeedKmph": "12",
            "weatherDesc": [{"value": "Partly cloudy"}],
            "lang_zh": [{"value": "局部多云"}],
        }
    ],
    "nearest_area": [{"areaName": [{"value": "Beijing"}], "country": [{"value": "China"}]}],
    "weather": [{"mintempC": "18", "maxtempC": "30", "date": "2026-09-18"}],
}


def test_format_full_data() -> None:
    out = format_weather(_SAMPLE)
    assert "Beijing（China）" in out
    assert "局部多云" in out  # 优先中文描述
    assert "26°C" in out and "体感 28°C" in out
    assert "湿度 65%" in out
    assert "今日 18~30°C" in out


def test_format_falls_back_to_english_desc() -> None:
    data = {
        "current_condition": [
            {
                "temp_C": "20",
                "FeelsLikeC": "20",
                "humidity": "50",
                "windspeedKmph": "5",
                "weatherDesc": [{"value": "Sunny"}],
            }
        ]
    }
    assert "Sunny" in format_weather(data)


def test_format_empty_current_condition_raises() -> None:
    with pytest.raises(ToolError, match="天气数据为空"):
        format_weather({})


def test_format_without_area_uses_default_location() -> None:
    data = {
        "current_condition": [
            {
                "temp_C": "22",
                "FeelsLikeC": "22",
                "humidity": "40",
                "windspeedKmph": "3",
                "lang_zh": [{"value": "晴"}],
            }
        ]
    }
    assert format_weather(data).startswith("当前位置：晴")


def test_execute_uses_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def _fake_fetch(city: str):
        seen["city"] = city
        return _SAMPLE

    monkeypatch.setattr("fairy.tools.weather.fetch_weather", _fake_fetch)
    tool = WeatherTool()
    out = tool.execute("北京")
    assert seen["city"] == "北京"
    assert "局部多云" in out


def test_execute_default_city_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def _fake_fetch(city: str):
        seen["city"] = city
        return _SAMPLE

    monkeypatch.setattr("fairy.tools.weather.fetch_weather", _fake_fetch)
    WeatherTool().execute()
    assert seen["city"] == ""


def test_fetch_weather_network_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("timeout")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ToolError, match="天气查询失败"):
        fetch_weather("北京")


def test_fetch_weather_bad_json_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.request

    class _Resp:
        def read(self) -> bytes:
            return io.BytesIO(b"not json").read()

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(ToolError, match="天气查询失败"):
        fetch_weather("")


def test_permission_is_network() -> None:
    assert WeatherTool().permission == "network"
