"""向量知识库：chunks 存进现有 memory.db，余弦相似度用 numpy 计算。

设计取舍：不引入 Chroma/FAISS——个人知识库规模（几千条 chunk）下，
全量 numpy 点积的检索耗时是毫秒级，零额外依赖换来的是部署简单。

嵌入向量以 float32 的 BLOB 存储（调用方须先 L2 归一化，点积即余弦）。
独立连接同一 ``memory.db``（WAL 模式下多连接安全）。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from fairy.memory.store import DB_FILENAME

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source, chunk_index)
);
"""

# 检索结果相似度下限：低于此值视为无关，不注入上下文
MIN_SCORE = 0.45


class VectorStore:
    """知识块存储与检索：``add_chunks`` / ``search`` / ``remove_source``。"""

    def __init__(self, data_dir: str | Path) -> None:
        data_dir = Path(data_dir).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(data_dir / DB_FILENAME, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_chunks(self, source: str, texts: list[str], embeddings: np.ndarray) -> int:
        """整体替换某来源的知识块（先删后插，天然去重/更新）。返回块数。"""
        if len(texts) != len(embeddings):
            raise ValueError("texts 与 embeddings 数量不一致")
        now = datetime.now(UTC).isoformat()
        rows = [
            (source, i, text, np.asarray(emb, dtype=np.float32).tobytes(), now)
            for i, (text, emb) in enumerate(zip(texts, embeddings, strict=True))
        ]
        with self._lock:
            self._conn.execute("DELETE FROM knowledge_chunks WHERE source = ?", (source,))
            self._conn.executemany(
                "INSERT INTO knowledge_chunks (source, chunk_index, text, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def remove_source(self, source: str) -> int:
        """删除某来源的全部知识块，返回删除条数。"""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM knowledge_chunks WHERE source = ?", (source,))
            self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_sources(self) -> list[tuple[str, int]]:
        """列出所有来源及其块数。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM knowledge_chunks GROUP BY source ORDER BY source"
            ).fetchall()
        return [(row["source"], row["n"]) for row in rows]

    def count(self) -> int:
        """知识块总数。"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM knowledge_chunks").fetchone()
        return int(row["n"])

    def search(self, query_embedding: np.ndarray, top_k: int = 3) -> list[tuple[float, str, str]]:
        """余弦相似度检索，返回 [(分数, 来源, 文本)]，按分数降序。

        查询与库存向量均为单位向量，点积即余弦。空库返回空列表。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, text, embedding FROM knowledge_chunks"
            ).fetchall()
        if not rows:
            return []

        matrix = np.array([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
        scores = matrix @ np.asarray(query_embedding, dtype=np.float32)
        order = np.argsort(scores)[::-1][:top_k]
        return [
            (float(scores[i]), rows[i]["source"], rows[i]["text"])
            for i in order
            if scores[i] >= MIN_SCORE
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
