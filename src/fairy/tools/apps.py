"""应用与项目启动工具：open_app / open_project（仅 Windows）。

- :class:`OpenAppTool`：按应用名解析快捷方式（.lnk）或注册表 App Paths，
  用 ``os.startfile`` 启动；解析成功后可把别名写入记忆层（preference
  键 ``alias:<小写名字>``），下次直接命中。
- :class:`OpenProjectTool`：在项目根目录（环境变量 ``FAIRY_PROJECT_ROOTS``）
  的第一层子目录里模糊匹配项目名，用 VSCode（``code``）打开。

仅使用标准库；非 Windows 平台抛出 :class:`ToolError`。
"""

from __future__ import annotations

import difflib
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

if TYPE_CHECKING:
    from fairy.memory.store import MemoryStore

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment]

# open_project 默认项目根目录（FAIRY_PROJECT_ROOTS 未设置时）
DEFAULT_PROJECT_ROOTS = ("~/Desktop/project", "~/projects", "~/code")

# VSCode 常见安装路径（相对于 %LOCALAPPDATA%）
_VSCODE_FALLBACK_REL = Path("Programs") / "Microsoft VS Code" / "bin" / "code.cmd"


def _ensure_windows() -> None:
    if sys.platform != "win32":
        raise ToolError("暂未支持该平台")


def _similarity(a: str, b: str) -> float:
    """两个名字的大小写不敏感相似度（difflib）。"""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _lnk_search_dirs() -> list[Path]:
    """.lnk 搜索目录：桌面（含 OneDrive 桌面）与两级开始菜单 Programs。"""
    home = Path.home()
    dirs = [
        home / "Desktop",
        home / "OneDrive" / "Desktop",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    dirs.append(Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"))
    return dirs


def _all_lnks(search_dirs: list[Path]) -> list[Path]:
    """递归收集搜索目录下的全部 .lnk 文件。"""
    lnks: list[Path] = []
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        try:
            lnks.extend(directory.rglob("*.lnk"))
        except OSError:
            continue
    return lnks


def _registry_app_paths(name: str) -> Path | None:
    """查注册表 App Paths（HKLM/HKCU）中的 ``<name>.exe``，返回存在的 exe 路径。"""
    if winreg is None:
        return None
    exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"
    subkey = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if value:
            candidate = Path(os.path.expandvars(str(value).strip('"')))
            if candidate.is_file():
                return candidate
    return None


class OpenAppTool(Tool):
    """按名字启动 Windows 应用（write 级权限）。

    解析顺序：别名记忆 → 桌面/开始菜单 .lnk 包含匹配 → 注册表 App Paths；
    多个候选时取文件名与目标名字 difflib 相似度最高者。
    """

    name = "open_app"
    description = "按应用名启动本机 Windows 应用（如「鸣潮」「微信」）"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "应用名，如「鸣潮」「微信」「WeChat」"},
        },
        "required": ["name"],
    }
    permission: PermissionLevel = "write"

    def __init__(self, memory: MemoryStore | None = None) -> None:
        self._memory = memory

    def auto_allow(self, args: dict[str, Any]) -> bool:
        """FAIRY_APP_WHITELIST（逗号分隔，大小写不敏感）内的应用名免确认。"""
        whitelist = os.environ.get("FAIRY_APP_WHITELIST", "")
        names = {item.strip().lower() for item in whitelist.split(",") if item.strip()}
        return str(args.get("name", "")).strip().lower() in names

    def execute(self, name: str) -> str:
        _ensure_windows()
        name = name.strip()
        if not name:
            raise ToolError("应用名不能为空。")
        alias_key = f"alias:{name.lower()}"

        # a) 别名记忆：命中且目标文件仍在时直接启动
        if self._memory is not None:
            alias_target = self._memory.get_preference(alias_key)
            if alias_target and Path(alias_target).is_file():
                os.startfile(alias_target)  # type: ignore[attr-defined]
                return f"已通过别名记忆启动应用「{name}」：{alias_target}"

        # b) 搜索候选：.lnk 文件名包含匹配 + 注册表 App Paths
        lnks = _all_lnks(_lnk_search_dirs())
        candidates = [p for p in lnks if name.lower() in p.stem.lower()]
        registry_hit = _registry_app_paths(name)
        if registry_hit is not None and registry_hit not in candidates:
            candidates.append(registry_hit)

        # e) 找不到：列出最接近的 5 个候选名
        if not candidates:
            pool = sorted({p.stem for p in lnks})
            closest = difflib.get_close_matches(name, pool, n=5, cutoff=0.0)
            hint = "、".join(closest) if closest else "（开始菜单与桌面均无快捷方式）"
            raise ToolError(f"找不到应用「{name}」。最接近的候选：{hint}")

        # c) 多候选时选文件名最接近的
        best = max(candidates, key=lambda p: _similarity(p.stem, name))

        # d) 启动并回写别名记忆
        os.startfile(str(best))  # type: ignore[attr-defined]
        if self._memory is not None:
            self._memory.set_preference(alias_key, str(best))

        extra = ""
        if len(candidates) > 1:
            others = "、".join(p.stem for p in candidates if p != best)
            extra = (
                f"（共 {len(candidates)} 个候选，已选择最接近的「{best.stem}」；其余：{others}）"
            )
        return f"已启动应用「{name}」：{best}{extra}"


class OpenProjectTool(Tool):
    """用编辑器打开项目目录（write 级权限）。

    在 ``FAIRY_PROJECT_ROOTS``（逗号分隔；默认 ``~/Desktop/project,~/projects,~/code``）
    各根目录的第一层子目录中匹配项目名：先做双向包含匹配（目录名含查询词，
    或查询词含目录名——用户说「kimi 桌宠这个项目」时查询词会比目录名长），
    无结果时 difflib 模糊兜底（相似度 ≥ 0.4 自动选最接近者）。
    支持编辑器参数：vscode（默认）/ idea。
    """

    name = "open_project"
    description = (
        "用编辑器打开指定名字的项目目录（如「用 VSCode 打开 kimi 桌宠项目」）。"
        "项目名给目录名的一部分即可（如「kimi」）；editor 参数可选 vscode（默认）或 idea。"
        "找不到时会返回最接近的候选名，请直接用候选名重试。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "项目名（目录名的子串即可，如「kimi」）"},
            "editor": {
                "type": "string",
                "enum": ["vscode", "idea"],
                "description": "用什么编辑器打开，默认 vscode",
            },
        },
        "required": ["name"],
    }
    permission: PermissionLevel = "write"

    def auto_allow(self, args: dict[str, Any]) -> bool:
        """打开项目目录一律需用户确认，不启用白名单。"""
        return False

    def _project_roots(self) -> list[Path]:
        raw = os.environ.get("FAIRY_PROJECT_ROOTS") or ",".join(DEFAULT_PROJECT_ROOTS)
        roots = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            path = Path(item).expanduser()
            if path.is_dir():
                roots.append(path)
        return roots

    def _find_code(self) -> str:
        """定位 VSCode 的 code 命令：PATH → 常见安装路径。"""
        code = shutil.which("code")
        if code:
            return code
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            fallback = Path(local_appdata) / _VSCODE_FALLBACK_REL
            if fallback.is_file():
                return str(fallback)
        raise ToolError("找不到 VSCode 的 code 命令（PATH 与常见安装路径均无），请先安装 VSCode。")

    def _find_editor(self, editor: str) -> str:
        """按编辑器名定位可执行文件。"""
        editor = (editor or "vscode").strip().lower()
        if editor in ("vscode", "code"):
            return self._find_code()
        if editor == "idea":
            found = shutil.which("idea64") or shutil.which("idea")
            if found:
                return found
            # 常见安装路径：Program Files 与 JetBrains Toolbox
            patterns = [
                r"C:\Program Files\JetBrains\IntelliJ IDEA*\bin\idea64.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\apps\*\*\*\bin\idea64.exe"),
            ]
            for pattern in patterns:
                hits = glob.glob(pattern)
                if hits:
                    return sorted(hits)[-1]  # 多版本取最后（通常最新）
            raise ToolError("找不到 IntelliJ IDEA（idea64.exe）。可以用 VSCode 重试。")
        raise ToolError(f"暂不支持的编辑器 {editor!r}，目前支持：vscode、idea。")

    def execute(self, name: str, editor: str = "vscode") -> str:
        _ensure_windows()
        name = name.strip()
        if not name:
            raise ToolError("项目名不能为空。")

        roots = self._project_roots()
        subdirs: list[Path] = []
        for root in roots:
            try:
                subdirs.extend(p for p in root.iterdir() if p.is_dir())
            except OSError:
                continue

        query = name.lower()
        # 双向包含：目录名含查询词，或查询词含目录名（「kimi桌宠这个项目」→「kimi桌宠」）
        matches = [p for p in subdirs if query in p.name.lower() or p.name.lower() in query]
        fuzzy_note = ""
        if not matches:
            # 模糊兜底：相似度足够高时自动选最接近者
            pool = sorted(subdirs, key=lambda p: _similarity(p.name, name), reverse=True)
            if pool and _similarity(pool[0].name, name) >= 0.4:
                matches = [pool[0]]
                fuzzy_note = f"（按相似度匹配到「{pool[0].name}」）"
        if not matches:
            pool_names = sorted({p.name for p in subdirs})
            closest = difflib.get_close_matches(name, pool_names, n=5, cutoff=0.0)
            hint = "、".join(closest) if closest else "（项目根目录为空或不存在）"
            raise ToolError(
                f"找不到项目「{name}」。最接近的候选：{hint}。"
                "请直接用候选名重试，无需带「项目」等后缀。"
            )

        best = max(matches, key=lambda p: _similarity(p.name, name))
        exe = self._find_editor(editor)
        try:
            subprocess.Popen([exe, str(best)])
        except OSError as exc:
            raise ToolError(f"启动编辑器失败：{exc}") from exc

        editor_name = "IntelliJ IDEA" if editor.lower() == "idea" else "VSCode"
        extra = ""
        if len(matches) > 1:
            others = "、".join(p.name for p in matches if p != best)
            extra = f"（共 {len(matches)} 个匹配，已选择最接近的「{best.name}」；其余：{others}）"
        return f"已用 {editor_name} 打开项目「{best.name}」：{best}{fuzzy_note}{extra}"
