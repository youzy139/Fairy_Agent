"""语音识别（STT）：faster-whisper 本地推理，音频不出本机。

模型懒加载——首次识别时才下载/装载（下载到 ``~/.fairy/models``），
避免启动 GUI 时白等。CPU 上用 int8 量化，速度足够按键说话场景。
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np


class SttError(RuntimeError):
    """模型加载或识别失败。"""


class SpeechToText:
    """faster-whisper 封装：``transcribe(audio) -> 文本``。

    :param model_size: 模型规格（tiny/base/small/…），中文建议 small 起步。
    :param model_dir: 模型缓存目录（默认 ``~/.fairy/models``）。
    :param language: 识别语言，默认中文；传 None 自动检测。
    """

    def __init__(
        self,
        model_size: str = "small",
        model_dir: Path | None = None,
        language: str | None = "zh",
    ) -> None:
        self._model_size = model_size
        self._model_dir = model_dir or Path("~/.fairy/models").expanduser()
        self._language = language
        self._model = None  # WhisperModel，懒加载
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray) -> str:
        """识别一段 16kHz float32 音频，返回拼接后的文本（可为空串）。"""
        if audio.size == 0:
            return ""
        model = self._ensure_model()
        try:
            segments, _info = model.transcribe(
                audio,
                language=self._language,
                vad_filter=True,  # 过滤首尾静音，减少幻听
            )
            return "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            raise SttError(f"语音识别失败：{exc}") from exc

    def _ensure_model(self):  # -> WhisperModel
        with self._lock:
            if self._model is None:
                self._model = self._load_model()
            return self._model

    def _load_model(self):  # -> WhisperModel
        """装载模型；直连 HuggingFace 失败时改用 hf-mirror 镜像重试一次。

        国内网络访问 huggingface.co 经常超时，镜像站 hf-mirror.com 是社区
        通用的公益镜像，仅用于下载公开的语音识别模型权重。
        """
        import os

        from faster_whisper import WhisperModel

        kwargs = {
            "device": "cpu",
            "compute_type": "int8",
            "download_root": str(self._model_dir),
        }
        try:
            return WhisperModel(self._model_size, **kwargs)
        except Exception as first:
            if os.environ.get("HF_ENDPOINT"):
                raise self._wrap_error(first) from first  # 已设镜像仍失败，不重试
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            try:
                return WhisperModel(self._model_size, **kwargs)
            except Exception as second:
                raise self._wrap_error(second) from second

    def _wrap_error(self, exc: Exception) -> SttError:
        return SttError(
            f"语音模型加载失败（{self._model_size}）：{exc}。首次使用需要联网下载模型，请检查网络。"
        )
