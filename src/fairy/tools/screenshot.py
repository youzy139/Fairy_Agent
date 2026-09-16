"""截屏工具：screenshot（read 级）。

截取屏幕画面保存到工作区 screenshots/ 目录（时间戳命名，永不覆盖）。
归为 read 级的理由：本质是「观察屏幕」的只读行为，副作用仅限于在工作区内
新增带时间戳的文件，不修改任何用户数据（见 docs/security.md）。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError
from fairy.tools.fs import _resolve_in_workspace


class ScreenshotTool(Tool):
    """截取当前屏幕，保存为 PNG。

    默认保存到工作区 ``screenshots/`` 目录；构造时可注入 ``output_dir``
    （如系统「图片/屏幕截图」目录）供 GUI 快捷动作使用——该参数只能由
    调用方代码注入，不在 LLM 可见的 JSON Schema 中，模型无法指定保存位置。
    """

    name = "screenshot"
    description = "截取当前屏幕画面，保存为 PNG 文件到工作区 screenshots/ 目录，返回文件路径"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "all_screens": {
                "type": "boolean",
                "description": "是否截取所有显示器（默认 false，仅主屏）",
            }
        },
        "required": [],
    }
    permission: PermissionLevel = "read"

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        output_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._output_dir = Path(output_dir).expanduser() if output_dir else None

    def execute(self, all_screens: bool = False) -> str:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise ToolError("截屏功能需要 Pillow：pip install pillow") from exc

        try:
            image = ImageGrab.grab(all_screens=all_screens)
        except OSError as exc:
            raise ToolError(f"截屏失败：{exc}") from exc

        if self._output_dir is not None:
            out_dir = self._output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = _resolve_in_workspace(self._workspace, "screenshots")
            out_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("screenshot-%Y%m%d-%H%M%S.png")
        out_path = out_dir / filename
        # 时间戳碰撞时追加序号，绝不覆盖已有文件
        seq = 1
        while out_path.exists():
            out_path = out_dir / f"{filename[:-4]}-{seq}.png"
            seq += 1
        image.save(out_path, format="PNG")
        return f"截屏已保存：{out_path}（{image.width}x{image.height}）"
