"""单元测试：MemoryStore 的建库建表、会话/消息 CRUD、偏好、项目上下文与并发安全。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from fairy.memory.store import DB_FILENAME, MemoryStore


def test_creates_db_and_tables(tmp_path: Path) -> None:
    """构造时创建数据库文件与全部四张表，且开启 WAL 模式。"""
    store = MemoryStore(tmp_path)
    try:
        assert store.db_path == tmp_path / DB_FILENAME
        assert store.db_path.exists()

        rows = store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        tables = {row["name"] for row in rows}
        assert {"sessions", "messages", "preferences", "project_context"} <= tables

        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        store.close()


def test_session_crud(tmp_path: Path) -> None:
    """create_session 返回递增 id；list_sessions 含 id/时间/标题/消息数且新的在前。"""
    store = MemoryStore(tmp_path)
    try:
        sid1 = store.create_session("/ws/a")
        sid2 = store.create_session("/ws/b", title="第二个会话")
        assert sid1 != sid2

        store.append_message(sid1, {"role": "user", "content": "你好"})
        store.append_message(sid1, {"role": "assistant", "content": "你好呀"})

        sessions = store.list_sessions()
        assert [s["id"] for s in sessions] == [sid2, sid1]
        by_id = {s["id"]: s for s in sessions}
        assert by_id[sid1]["title"] is None
        assert by_id[sid1]["workspace"] == "/ws/a"
        assert by_id[sid1]["message_count"] == 2
        assert by_id[sid1]["created_at"]
        assert by_id[sid2]["title"] == "第二个会话"
        assert by_id[sid2]["message_count"] == 0

        # limit 生效
        assert len(store.list_sessions(limit=1)) == 1

        # 标题可后续更新
        store.set_session_title(sid1, "首条消息前二十字")
        assert store.list_sessions()[1]["title"] == "首条消息前二十字"
    finally:
        store.close()


def test_messages_persist_across_reopen(tmp_path: Path) -> None:
    """消息写入后关闭并重开数据库，payload 完整恢复（含 tool_calls 字段）。"""
    messages = [
        {"role": "system", "content": "你是 Fairy。"},
        {"role": "user", "content": "看看目录"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "a.txt"},
        {"role": "assistant", "content": "目录里有 a.txt。"},
    ]

    store = MemoryStore(tmp_path)
    sid = store.create_session("/ws")
    for msg in messages:
        store.append_message(sid, msg)
    store.close()

    # 新实例重开同一数据库
    store2 = MemoryStore(tmp_path)
    try:
        assert store2.get_messages(sid) == messages
        # 不存在的会话返回空列表
        assert store2.get_messages(9999) == []
    finally:
        store2.close()


def test_preferences(tmp_path: Path) -> None:
    """偏好读写：默认值、覆盖更新、跨重开保留。"""
    store = MemoryStore(tmp_path)
    try:
        assert store.get_preference("theme") is None
        assert store.get_preference("theme", "dark") == "dark"

        store.set_preference("theme", "light")
        assert store.get_preference("theme", "dark") == "light"

        store.set_preference("theme", "blue")
        assert store.get_preference("theme") == "blue"
    finally:
        store.close()

    store2 = MemoryStore(tmp_path)
    try:
        assert store2.get_preference("theme") == "blue"
    finally:
        store2.close()


def test_project_context(tmp_path: Path) -> None:
    """项目上下文按 (workspace, key) 隔离，可覆盖更新。"""
    store = MemoryStore(tmp_path)
    try:
        assert store.get_context("/ws/a", "lang") is None
        assert store.get_context("/ws/a", "lang", "unknown") == "unknown"

        store.set_context("/ws/a", "lang", "python")
        store.set_context("/ws/b", "lang", "rust")
        assert store.get_context("/ws/a", "lang") == "python"
        assert store.get_context("/ws/b", "lang") == "rust"

        store.set_context("/ws/a", "lang", "python3.13")
        assert store.get_context("/ws/a", "lang") == "python3.13"
        # 同 workspace 不同 key 互不影响
        store.set_context("/ws/a", "framework", "pytest")
        assert store.get_context("/ws/a", "framework") == "pytest"
        assert store.get_context("/ws/a", "lang") == "python3.13"
    finally:
        store.close()


def test_concurrent_appends(tmp_path: Path) -> None:
    """多线程并发写入同一会话：无异常且消息一条不丢。"""
    store = MemoryStore(tmp_path)
    sid = store.create_session("/ws")
    thread_count, per_thread = 8, 25

    def worker(tag: int) -> None:
        for i in range(per_thread):
            store.append_message(sid, {"role": "user", "content": f"t{tag}-{i}"})
            store.set_preference(f"k{tag}", str(i))
            store.set_context("/ws", f"k{tag}", str(i))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        messages = store.get_messages(sid)
        assert len(messages) == thread_count * per_thread
        # 每个线程的消息各自保持顺序
        for tag in range(thread_count):
            seq = [m["content"] for m in messages if m["content"].startswith(f"t{tag}-")]
            assert seq == [f"t{tag}-{i}" for i in range(per_thread)]
        # 连接仍可用
        assert store.get_preference("k0") is not None
        assert store.get_context("/ws", "k0") is not None
    finally:
        store.close()


def test_close_then_use_raises(tmp_path: Path) -> None:
    """close 后连接关闭，再操作抛出 sqlite3 错误（不静默成功）。"""
    store = MemoryStore(tmp_path)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.list_sessions()
