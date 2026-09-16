"""审计日志：所有工具调用追加写入 ``{FAIRY_DATA_DIR}/audit.jsonl``。

每行一条 JSON，字段：``time``（ISO8601）、``tool``、``args``、``allowed``，
以及 ``deny_reason`` 或 ``result_summary``。目录不存在时自动创建；
写入失败只告警，不中断主流程。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUDIT_FILENAME = "audit.jsonl"

# 摘要字段的最大长度，防止审计日志被超大结果撑爆
_SUMMARY_MAX_LEN = 500


class AuditLogger:
    """JSONL 审计日志写入器。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._path = Path(data_dir).expanduser() / AUDIT_FILENAME

    @property
    def path(self) -> Path:
        """审计日志文件路径。"""
        return self._path

    def log(
        self,
        tool: str,
        args: dict[str, Any],
        allowed: bool,
        deny_reason: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        """追加一条审计记录；任何写入失败只告警不抛异常。"""
        record: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "tool": tool,
            "args": _safe_jsonable(args),
            "allowed": allowed,
        }
        if deny_reason is not None:
            record["deny_reason"] = deny_reason
        if result_summary is not None:
            record["result_summary"] = result_summary[:_SUMMARY_MAX_LEN]

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("审计日志写入失败（%s）：%s", self._path, exc)


def _safe_jsonable(args: dict[str, Any]) -> dict[str, Any]:
    """把参数转成可 JSON 序列化的形式，失败时降级为 repr。"""
    try:
        json.dumps(args)
    except (TypeError, ValueError):
        return {key: repr(value) for key, value in args.items()}
    return args
