"""SQLite 记忆存储：会话、消息、用户偏好与项目上下文。

仅用标准库 ``sqlite3``。数据库文件为 ``{data_dir}/memory.db``，开启 WAL
模式；连接以 ``check_same_thread=False`` 创建并配合 ``threading.Lock``
保证基本线程安全（GUI 存在后台线程）。所有写操作即时提交事务。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_FILENAME = "memory.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    workspace TEXT NOT NULL,
    title TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_context (
    workspace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace, key)
);
"""


def _now() -> str:
    """当前 UTC 时间的 ISO8601 字符串（与审计日志口径一致）。"""
    return datetime.now(UTC).isoformat()


class MemoryStore:
    """SQLite 记忆存储：会话/消息持久化、偏好与项目上下文读写。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / DB_FILENAME
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @property
    def db_path(self) -> Path:
        """数据库文件路径。"""
        return self._db_path

    # ------------------------------------------------------------------
    # 会话与消息
    # ------------------------------------------------------------------
    def create_session(self, workspace: str, title: str | None = None) -> int:
        """新建会话，返回会话 id。"""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO sessions (created_at, workspace, title) VALUES (?, ?, ?)",
                (_now(), workspace, title),
            )
            self._conn.commit()
        return int(cursor.lastrowid)

    def set_session_title(self, session_id: int, title: str) -> None:
        """更新会话标题（通常用首条用户消息的前若干字）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            self._conn.commit()

    def append_message(self, session_id: int, message: dict[str, Any]) -> None:
        """追加一条消息，payload 为消息 dict 的 JSON（保留 tool_calls 等字段）。"""
        payload = json.dumps(message, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, payload, created_at) VALUES (?, ?, ?, ?)",
                (session_id, str(message.get("role", "")), payload, _now()),
            )
            self._conn.commit()

    def get_messages(self, session_id: int) -> list[dict[str, Any]]:
        """按写入顺序取回会话的全部消息（payload 反序列化为 dict）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近的会话（新的在前），含 id/created_at/workspace/title/消息数。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id, s.created_at, s.workspace, s.title,
                       COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 用户偏好
    # ------------------------------------------------------------------
    def set_preference(self, key: str, value: str) -> None:
        """写入（或覆盖）一条用户偏好。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, _now()),
            )
            self._conn.commit()

    def get_preference(self, key: str, default: str | None = None) -> str | None:
        """读取用户偏好，不存在时返回 default。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM preferences WHERE key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row is not None else default

    # ------------------------------------------------------------------
    # 项目上下文
    # ------------------------------------------------------------------
    def set_context(self, workspace: str, key: str, value: str) -> None:
        """写入（或覆盖）某工作区的一条项目上下文。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO project_context (workspace, key, value, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace, key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (workspace, key, value, _now()),
            )
            self._conn.commit()

    def get_context(self, workspace: str, key: str, default: str | None = None) -> str | None:
        """读取某工作区的项目上下文，不存在时返回 default。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM project_context WHERE workspace = ? AND key = ?",
                (workspace, key),
            ).fetchone()
        return row["value"] if row is not None else default

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            self._conn.close()
