"""桌面整理工具：organize_desktop（dangerous 级，批量文件移动）。

按扩展名把桌面上的散文件归类到「图片/文档/视频/音频/压缩包/安装程序/
快捷方式/代码/其他」文件夹。默认 dry_run 先出方案，确认后才真正移动。

安全边界：
- 只处理桌面顶层文件，不碰子目录内容、不移动文件夹本身；
- 跳过隐藏文件与 desktop.ini 等系统文件；
- 重名自动追加序号，绝不覆盖；
- 仅能在桌面目录内操作，路径校验失败即拒绝。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

# 扩展名 → 分类文件夹
_CATEGORY_MAP: dict[str, str] = {
    # 图片
    ".jpg": "图片",
    ".jpeg": "图片",
    ".png": "图片",
    ".gif": "图片",
    ".bmp": "图片",
    ".webp": "图片",
    ".svg": "图片",
    ".ico": "图片",
    # 文档
    ".txt": "文档",
    ".md": "文档",
    ".doc": "文档",
    ".docx": "文档",
    ".pdf": "文档",
    ".xls": "文档",
    ".xlsx": "文档",
    ".ppt": "文档",
    ".pptx": "文档",
    ".csv": "文档",
    # 视频
    ".mp4": "视频",
    ".mkv": "视频",
    ".avi": "视频",
    ".mov": "视频",
    ".wmv": "视频",
    ".flv": "视频",
    # 音频
    ".mp3": "音频",
    ".wav": "音频",
    ".flac": "音频",
    ".aac": "音频",
    ".ogg": "音频",
    ".m4a": "音频",
    # 压缩包
    ".zip": "压缩包",
    ".rar": "压缩包",
    ".7z": "压缩包",
    ".tar": "压缩包",
    ".gz": "压缩包",
    # 安装程序
    ".exe": "安装程序",
    ".msi": "安装程序",
    ".bat": "安装程序",
    ".cmd": "安装程序",
    # 快捷方式
    ".lnk": "快捷方式",
    ".url": "快捷方式",
    # 代码
    ".py": "代码",
    ".js": "代码",
    ".ts": "代码",
    ".java": "代码",
    ".c": "代码",
    ".cpp": "代码",
    ".h": "代码",
    ".go": "代码",
    ".rs": "代码",
    ".html": "代码",
    ".css": "代码",
}
_DEFAULT_CATEGORY = "其他"

# 永不移动的系统文件与自身程序
_SKIP_FILES = {"desktop.ini", "thumbs.db", "fairy.exe"}

# 桌面目录候选（考虑 OneDrive 重定向）
_DESKTOP_CANDIDATES = (
    "~/Desktop",
    "~/OneDrive/Desktop",
    "~/OneDrive/桌面",
)


def find_desktop() -> Path:
    """探测真实桌面目录，找不到时抛 ToolError。"""
    for candidate in _DESKTOP_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_dir():
            return path
    raise ToolError("未找到桌面目录（尝试了 " + "、".join(_DESKTOP_CANDIDATES) + "）。")


def _categorize(file: Path) -> str:
    return _CATEGORY_MAP.get(file.suffix.lower(), _DEFAULT_CATEGORY)


class OrganizeDesktopTool(Tool):
    """整理桌面：把散文件按类型归类到文件夹。dry_run 默认开启。"""

    name = "organize_desktop"
    description = (
        "整理电脑桌面：把桌面上的散文件按类型（图片/文档/视频等）移动到对应分类文件夹。"
        "默认 dry_run=true 只给出整理方案不移动文件；确认方案后再用 dry_run=false 执行。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "description": "true（默认）只预览方案；false 才真正移动文件",
            }
        },
        "required": [],
    }
    permission: PermissionLevel = "dangerous"

    def __init__(self, desktop: str | os.PathLike[str] | None = None) -> None:
        # 默认 None：延迟到 execute 时再探测，避免构造期在无桌面环境直接失败
        self._desktop = Path(desktop).expanduser() if desktop else None

    def _scan(self, desktop: Path) -> dict[str, list[Path]]:
        """扫描桌面顶层文件并归类；目录、隐藏文件、系统文件跳过。"""
        if not desktop.is_dir():
            raise ToolError(f"桌面目录不存在：{desktop}")
        plan: dict[str, list[Path]] = {}
        for entry in sorted(desktop.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.startswith(".") or entry.name.lower() in _SKIP_FILES:
                continue
            plan.setdefault(_categorize(entry), []).append(entry)
        return plan

    def execute(self, dry_run: bool = True) -> str:
        desktop = self._desktop or find_desktop()
        plan = self._scan(desktop)
        if not plan:
            return "桌面上没有需要整理的文件。"

        total = sum(len(files) for files in plan.values())
        lines = [f"桌面共 {total} 个散文件，整理方案："]
        for category, files in sorted(plan.items()):
            lines.append(
                f"  [{category}] {len(files)} 个："
                + "、".join(f.name for f in files[:10])
                + (" …" if len(files) > 10 else "")
            )

        if dry_run:
            lines.append("（预览模式，未移动任何文件；确认后请用 dry_run=false 执行）")
            return "\n".join(lines)

        moved = 0
        for category, files in plan.items():
            target_dir = desktop / category
            target_dir.mkdir(exist_ok=True)
            for file in files:
                shutil.move(str(file), str(_unique_path(target_dir / file.name)))
                moved += 1
        lines.append(f"已完成整理：共移动 {moved} 个文件。")
        return "\n".join(lines)


def _unique_path(target: Path) -> Path:
    """重名时追加序号：name (1).ext、name (2).ext……绝不覆盖。"""
    if not target.exists():
        return target
    for i in range(1, 1000):
        candidate = target.with_name(f"{target.stem} ({i}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise ToolError(f"无法为 {target.name} 生成不冲突的文件名。")
