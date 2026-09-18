"""voice 子系统单元测试：录音器状态机、STT/TTS 封装（外部依赖全部 mock）。

不触碰真实麦克风、模型与网络——sounddevice / faster_whisper / edge_tts
均以假模块注入 sys.modules。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from fairy.voice import VOICE_MISSING_HINT, voice_available
from fairy.voice.recorder import PushToTalkRecorder, RecorderError
from fairy.voice.stt import SpeechToText, SttError
from fairy.voice.tts import TextToSpeech, TtsError, truncate_for_speech


# ------------------------------------------------------------------
# voice_available
# ------------------------------------------------------------------
def test_voice_available_matches_imports() -> None:
    """与真实 import 结果一致（本环境已装依赖时为 True）。"""
    try:
        import edge_tts  # noqa: F401
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401

        expected = True
    except ImportError:
        expected = False
    assert voice_available() is expected


def test_voice_missing_hint_mentions_extra() -> None:
    assert "voice" in VOICE_MISSING_HINT


# ------------------------------------------------------------------
# PushToTalkRecorder
# ------------------------------------------------------------------
class _FakeStream:
    """模拟 sounddevice.InputStream：start 后回调推送固定数据。"""

    instances: list[_FakeStream] = []

    def __init__(self, samplerate: int, channels: int, dtype: str, callback) -> None:
        self.callback = callback
        self.started = False
        _FakeStream.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        pass

    def feed(self, data: np.ndarray) -> None:
        """模拟音频回调（N 帧单声道）。"""
        self.callback(data.reshape(-1, 1), len(data), None, None)


@pytest.fixture()
def fake_sd(monkeypatch: pytest.MonkeyPatch):
    _FakeStream.instances = []
    module = types.SimpleNamespace(InputStream=_FakeStream)
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


def test_record_concatenates_chunks(fake_sd) -> None:
    rec = PushToTalkRecorder()
    rec.start()
    stream = _FakeStream.instances[-1]
    stream.feed(np.ones(1600, dtype=np.float32))
    stream.feed(np.zeros(800, dtype=np.float32))
    audio = rec.stop()
    assert len(audio) == 2400
    assert audio[0] == pytest.approx(1.0)
    assert audio[-1] == pytest.approx(0.0)
    assert rec.duration(audio) == pytest.approx(2400 / 16000)


def test_stop_without_data_returns_empty(fake_sd) -> None:
    rec = PushToTalkRecorder()
    rec.start()
    audio = rec.stop()
    assert audio.size == 0
    assert not rec.recording


def test_restart_discards_old_data(fake_sd) -> None:
    rec = PushToTalkRecorder()
    rec.start()
    _FakeStream.instances[-1].feed(np.ones(800, dtype=np.float32))
    rec.start()  # 重复 start 视为重新开始
    assert len(_FakeStream.instances) == 2
    _FakeStream.instances[-1].feed(np.ones(400, dtype=np.float32))
    assert len(rec.stop()) == 400


def test_start_failure_raises_recorder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadStream:
        def __init__(self, **kwargs) -> None:
            raise OSError("no device")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=_BadStream))
    with pytest.raises(RecorderError, match="麦克风"):
        PushToTalkRecorder().start()


# ------------------------------------------------------------------
# SpeechToText
# ------------------------------------------------------------------
class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisperModel:
    def __init__(self, size: str, **kwargs) -> None:
        self.size = size

    def transcribe(self, audio: np.ndarray, **kwargs):
        return [_FakeSegment("你好"), _FakeSegment(" Fairy")], None


@pytest.fixture()
def fake_whisper(monkeypatch: pytest.MonkeyPatch):
    module = types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return module


def test_transcribe_joins_segments(fake_whisper, tmp_path: Path) -> None:
    stt = SpeechToText(model_size="small", model_dir=tmp_path)
    audio = np.zeros(16000, dtype=np.float32)
    assert stt.transcribe(audio) == "你好 Fairy"


def test_transcribe_empty_audio_short_circuits(fake_whisper, tmp_path: Path) -> None:
    stt = SpeechToText(model_dir=tmp_path)
    assert stt.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert stt._model is None  # 空音频不触发模型加载


def test_model_lazy_loaded_once(fake_whisper, tmp_path: Path) -> None:
    stt = SpeechToText(model_dir=tmp_path)
    assert stt._model is None
    audio = np.zeros(1600, dtype=np.float32)
    stt.transcribe(audio)
    model = stt._model
    stt.transcribe(audio)
    assert stt._model is model  # 不重复加载


def test_model_load_failure_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _BoomModel:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("disk full")

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_BoomModel)
    )
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")  # 跳过镜像重试路径
    stt = SpeechToText(model_dir=tmp_path)
    with pytest.raises(SttError, match="模型加载失败"):
        stt.transcribe(np.zeros(1600, dtype=np.float32))


def test_download_falls_back_to_mirror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """直连失败后自动切 hf-mirror 重试。"""
    calls: list[str] = []

    class _FlakyModel:
        def __init__(self, *args, **kwargs) -> None:
            import os

            calls.append(os.environ.get("HF_ENDPOINT", ""))
            if len(calls) == 1:
                raise ConnectionError("huggingface.co timeout")

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FlakyModel)
    )
    stt = SpeechToText(model_dir=tmp_path)
    stt._ensure_model()
    assert calls == ["", "https://hf-mirror.com"]


# ------------------------------------------------------------------
# TextToSpeech
# ------------------------------------------------------------------
def test_truncate_short_text_unchanged() -> None:
    assert truncate_for_speech("好的") == "好的"


def test_truncate_long_text_appends_hint() -> None:
    text = "字" * 400
    out = truncate_for_speech(text, max_chars=300)
    assert out.startswith("字" * 100)
    assert len(out) > 300  # 截断 + 省略提示
    assert "完整内容请看屏幕" in out


class _FakeCommunicate:
    """模拟 edge_tts.Communicate：save 写入假 MP3 内容。"""

    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice

    async def save(self, path: str) -> None:
        Path(path).write_bytes(b"FAKE-MP3")


@pytest.fixture()
def fake_edge_tts(monkeypatch: pytest.MonkeyPatch):
    module = types.SimpleNamespace(Communicate=_FakeCommunicate)
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return module


def test_synthesize_writes_file(fake_edge_tts, tmp_path: Path) -> None:
    tts = TextToSpeech(cache_dir=tmp_path)
    out = tts.synthesize("你好")
    assert out.read_bytes() == b"FAKE-MP3"


def test_synthesize_cache_hit_skips_network(fake_edge_tts, tmp_path: Path) -> None:
    tts = TextToSpeech(cache_dir=tmp_path)
    first = tts.synthesize("你好")
    first.write_bytes(b"CACHED")  # 篡改缓存内容以证明第二次没重写
    assert tts.synthesize("你好").read_bytes() == b"CACHED"


def test_synthesize_empty_text_raises(fake_edge_tts, tmp_path: Path) -> None:
    with pytest.raises(TtsError, match="没有可朗读的文本"):
        TextToSpeech(cache_dir=tmp_path).synthesize("   ")


def test_synthesize_failure_cleans_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FailCommunicate:
        def __init__(self, text: str, voice: str) -> None:
            pass

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"PARTIAL")
            raise ConnectionError("offline")

    monkeypatch.setitem(
        sys.modules, "edge_tts", types.SimpleNamespace(Communicate=_FailCommunicate)
    )
    tts = TextToSpeech(cache_dir=tmp_path)
    with pytest.raises(TtsError, match="语音合成失败"):
        tts.synthesize("你好")
    assert not list(tmp_path.glob("*.mp3"))  # 半截文件已清理


# ------------------------------------------------------------------
# 配置项
# ------------------------------------------------------------------
def test_voice_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("FAIRY_VOICE", "FAIRY_STT_MODEL", "FAIRY_TTS", "FAIRY_TTS_VOICE"):
        monkeypatch.delenv(var, raising=False)
    from fairy.config import load_settings

    settings = load_settings(dotenv_path=tmp_path / "no-such.env")
    assert settings.voice_enabled is False
    assert settings.stt_model == "small"
    assert settings.tts_enabled is True
    assert settings.tts_voice == "zh-CN-XiaoxiaoNeural"


def test_voice_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAIRY_VOICE", "true")
    monkeypatch.setenv("FAIRY_STT_MODEL", "base")
    monkeypatch.setenv("FAIRY_TTS", "false")
    monkeypatch.setenv("FAIRY_TTS_VOICE", "zh-CN-YunxiNeural")
    from fairy.config import load_settings

    settings = load_settings(dotenv_path=tmp_path / "no-such.env")
    assert settings.voice_enabled is True
    assert settings.stt_model == "base"
    assert settings.tts_enabled is False
    assert settings.tts_voice == "zh-CN-YunxiNeural"
