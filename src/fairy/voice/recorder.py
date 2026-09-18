"""按键录音：sounddevice 采集麦克风，start/stop 切换式（对讲机模式）。

录音在后台回调中持续累积 16kHz 单声道 float32 采样，``stop()`` 返回完整
numpy 数组供 STT 识别。不做自动静音截断——何时开始/结束完全由用户按键决定，
简单可靠、不会截掉话头。
"""

from __future__ import annotations

import threading

import numpy as np

# Whisper 要求的采样率
SAMPLE_RATE = 16000


class RecorderError(RuntimeError):
    """录音设备不可用等错误。"""


class PushToTalkRecorder:
    """切换式录音器：``start()`` 开始，``stop()`` 结束并返回音频。

    线程安全：start/stop 可从 GUI 线程调用，音频回调在 sounddevice 线程执行。
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._stream = None  # sounddevice.InputStream | None
        self._chunks: list[np.ndarray] = []

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    def start(self) -> None:
        """开始录音；重复调用视为重新开始（丢弃旧数据）。"""
        import sounddevice as sd

        with self._lock:
            self._close_stream_locked()
            self._chunks = []
            try:
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._on_audio,
                )
                self._stream.start()
            except Exception as exc:
                self._stream = None
                raise RecorderError(f"无法打开麦克风：{exc}") from exc

    def stop(self) -> np.ndarray:
        """结束录音，返回拼接后的音频（可能为空数组）。"""
        with self._lock:
            self._close_stream_locked()
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def duration(self, audio: np.ndarray) -> float:
        """音频时长（秒）。"""
        return len(audio) / self._sample_rate

    def _on_audio(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def _close_stream_locked(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
