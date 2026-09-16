"""audit.py 单元测试：日志格式、字段完整性、目录自动创建。"""

from __future__ import annotations

import json
from pathlib import Path

from fairy.safety.audit import AuditLogger


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_creates_directory_and_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "fairy-data"
    assert not data_dir.exists()

    audit = AuditLogger(data_dir)
    audit.log(tool="list_dir", args={"path": "."}, allowed=True, result_summary="ok")

    assert audit.path.exists()
    assert audit.path == data_dir / "audit.jsonl"


def test_log_fields_allowed(tmp_path: Path) -> None:
    audit = AuditLogger(tmp_path)
    audit.log(tool="read_file", args={"path": "a.txt"}, allowed=True, result_summary="内容摘要")

    (record,) = _read_records(audit.path)
    assert record["tool"] == "read_file"
    assert record["args"] == {"path": "a.txt"}
    assert record["allowed"] is True
    assert record["result_summary"] == "内容摘要"
    assert "deny_reason" not in record
    # ISO8601 时间戳可被解析
    from datetime import datetime

    datetime.fromisoformat(record["time"])


def test_log_fields_denied(tmp_path: Path) -> None:
    audit = AuditLogger(tmp_path)
    audit.log(
        tool="run_command",
        args={"command": "rm -rf /"},
        allowed=False,
        deny_reason="用户拒绝",
    )

    (record,) = _read_records(audit.path)
    assert record["allowed"] is False
    assert record["deny_reason"] == "用户拒绝"
    assert "result_summary" not in record


def test_log_appends_multiple_records(tmp_path: Path) -> None:
    audit = AuditLogger(tmp_path)
    audit.log(tool="a", args={}, allowed=True)
    audit.log(tool="b", args={}, allowed=False, deny_reason="no")

    records = _read_records(audit.path)
    assert [r["tool"] for r in records] == ["a", "b"]


def test_result_summary_truncated(tmp_path: Path) -> None:
    audit = AuditLogger(tmp_path)
    audit.log(tool="read_file", args={}, allowed=True, result_summary="x" * 1000)

    (record,) = _read_records(audit.path)
    assert len(record["result_summary"]) == 500


def test_write_failure_does_not_raise(tmp_path: Path, caplog) -> None:
    """写入失败只告警不崩溃。"""
    audit = AuditLogger(tmp_path)
    # 用一个已存在的同名文件占位，使 mkdir 失败
    audit._path.parent.mkdir(parents=True, exist_ok=True)
    audit._path.write_text("occupied", encoding="utf-8")
    blocked = AuditLogger(tmp_path / "audit.jsonl" / "sub")  # 父路径上是文件，mkdir 必失败

    with caplog.at_level("WARNING"):
        blocked.log(tool="x", args={}, allowed=True)  # 不应抛异常
    assert "审计日志写入失败" in caplog.text
