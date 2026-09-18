"""向量嵌入：fastembed 本地推理（ONNX，无需 torch/GPU）。

默认模型 BAAI/bge-small-zh-v1.5（中文优化，约 100MB），首次使用下载到
``~/.fairy/models``；直连 HuggingFace 失败时自动切 hf-mirror 镜像（与语音
模型同一策略）。依赖为可选额外组件 ``fairy-agent[rag]``，未安装时知识库
功能优雅降级。
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


def rag_available() -> bool:
    """RAG 依赖是否可用（fastembed）。"""
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


# 依赖缺失时给用户的提示
RAG_MISSING_HINT = "知识库依赖未安装，请先执行：pip install fairy-agent[rag]"


class EmbeddingError(RuntimeError):
    """模型加载或嵌入计算失败。"""


class Embedder:
    """fastembed 封装：``embed(texts) -> 单位向量数组``（懒加载，线程安全）。

    输出做 L2 归一化，使点积即余弦相似度，检索端无需再算模长。
    """

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, model_dir: Path | None = None) -> None:
        self._model_name = model
        self._model_dir = model_dir or Path("~/.fairy/models").expanduser()
        self._model = None  # fastembed.TextEmbedding，懒加载
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        """嵌入一批文本，返回 shape (N, dim) 的单位向量数组。"""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        model = self._ensure_model()
        try:
            vectors = np.array(list(model.embed(texts)), dtype=np.float32)
        except Exception as exc:
            raise EmbeddingError(f"向量计算失败：{exc}") from exc
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    def embed_one(self, text: str) -> np.ndarray:
        """嵌入单条文本，返回 shape (dim,) 的单位向量。"""
        return self.embed([text])[0]

    def _ensure_model(self):  # -> fastembed.TextEmbedding
        with self._lock:
            if self._model is None:
                self._model = self._load_model()
            return self._model

    def _load_model(self):  # -> fastembed.TextEmbedding
        """装载模型；直连 HuggingFace 失败时改用 hf-mirror 镜像重试一次。"""
        import os

        from fastembed import TextEmbedding

        kwargs = {"cache_dir": str(self._model_dir)}
        try:
            return TextEmbedding(self._model_name, **kwargs)
        except Exception as first:
            if os.environ.get("HF_ENDPOINT"):
                raise self._wrap_error(first) from first  # 已设镜像仍失败，不重试
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            try:
                return TextEmbedding(self._model_name, **kwargs)
            except Exception as second:
                raise self._wrap_error(second) from second

    def _wrap_error(self, exc: Exception) -> EmbeddingError:
        return EmbeddingError(
            f"向量模型加载失败（{self._model_name}）：{exc}。首次使用需要联网下载模型，请检查网络。"
        )
