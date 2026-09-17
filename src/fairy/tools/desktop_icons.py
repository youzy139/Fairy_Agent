"""桌面图标排列工具：list_desktop_icons（read 级）与 arrange_desktop（dangerous 级）。

与 organize_desktop 不同，本模块**不移动任何文件**，只通过桌面 ListView
控件改变图标在桌面上的摆放位置，按软件语义分组排列（例如把米哈游的
游戏按顺序挨在一起摆成一列）。

backend 可注入：默认使用 _desktop_listview 的真实 win32 实现（接口为
find_desktop_listview / list_icons / set_icon_position / is_auto_arrange /
set_auto_arrange 五个模块级函数），测试时传入内存桩即可。
"""

from __future__ import annotations

from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

# 布局参数：每组一列从左到右，组内从上到下。
# 注意：起点不是固定值——多显示器下桌面 ListView 的 (0,0) 可能落在副屏区域，
# 固定起点会把图标「搬」到用户看不到的屏幕上。布局原点改为现有图标包围盒的
# 左上角，即「在图标当前所在区域原地重排」（见 execute 中的计算）。
_FALLBACK_X = 40  # 防御用兜底起点（桌面无图标时不会走到布局）
_FALLBACK_Y = 40
_COLUMN_WIDTH = 110
_ROW_HEIGHT = 100
_OTHER_LABEL = "其他"


def _default_backend():
    """真实 win32 实现，延迟导入以便非 Windows 平台也能加载本模块。"""
    from fairy.tools import _desktop_listview

    return _desktop_listview


def _is_match(query: str, name: str) -> bool:
    """大小写不敏感匹配：先包含匹配，再退到字符顺序一致的子序列匹配。

    子序列兜底让口语缩写也能命中，例如「崩铁」匹配「崩坏：星穹铁道」
    （崩…铁 字符按序出现）。
    """
    q, n = query.casefold(), name.casefold()
    if q in n:
        return True
    it = iter(n)
    return all(ch in it for ch in q)


def _match_icons(
    groups: list[dict], desktop_icons: list[dict]
) -> tuple[list[tuple[str, list[dict]]], list[str]]:
    """按 groups 匹配桌面图标，返回 (分组方案, 警告列表)。

    匹配规则见 _is_match；每个桌面图标只被消费一次；
    匹配不到的名字记入警告；未出现在任何 group 的图标归入最后的
    「其他」组，保持原相对顺序。整组都没匹配到的空组不占用列。
    """
    remaining = list(desktop_icons)
    warnings: list[str] = []
    plan: list[tuple[str, list[dict]]] = []
    for group in groups:
        label = str(group.get("label", ""))
        members: list[dict] = []
        for query in group.get("icons") or []:
            hit = next((icon for icon in remaining if _is_match(str(query), icon["name"])), None)
            if hit is None:
                warnings.append(f"未在桌面找到与「{query}」匹配的图标。")
            else:
                remaining.remove(hit)
                members.append(hit)
        if members:
            plan.append((label, members))
    if remaining:
        plan.append((_OTHER_LABEL, remaining))
    return plan, warnings


def _assign_positions(
    plan: list[tuple[str, list[dict]]],
    origin_x: int = _FALLBACK_X,
    origin_y: int = _FALLBACK_Y,
) -> list[tuple[str, list[tuple[dict, int, int]]]]:
    """按布局算法为每个图标计算目标坐标。

    返回 [(label, [(icon, x, y), ...]), ...]，每组一列从左到右，
    组内按成员顺序从上到下。布局原点由调用方给出（现有图标包围盒左上角），
    保证多显示器下也在图标当前所在区域原地重排。
    """
    result: list[tuple[str, list[tuple[dict, int, int]]]] = []
    for col, (label, members) in enumerate(plan):
        x = origin_x + col * _COLUMN_WIDTH
        column = [(icon, x, origin_y + row * _ROW_HEIGHT) for row, icon in enumerate(members)]
        result.append((label, column))
    return result


def _validate_groups(groups: Any) -> list[dict]:
    if not isinstance(groups, list) or not groups:
        raise ToolError("参数 groups 必须是非空数组，每项形如 {label, icons}。")
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("icons"), list):
            raise ToolError("groups 的每项必须是 {label: str, icons: [str]} 结构。")
    return groups


class _BackendCaller:
    """把 backend 的一切异常统一转成中文 ToolError（ToolError 原样透传）。"""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def call(self, func: str, *args: Any) -> Any:
        try:
            return getattr(self._backend, func)(*args)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"操作桌面图标失败（{func}）：{exc}") from exc


class ListDesktopIconsTool(Tool):
    """列出桌面图标：名称、当前坐标、自动排列是否开启（只读）。"""

    name = "list_desktop_icons"
    description = (
        "列出桌面上的全部图标：名称、当前坐标，以及是否开启了自动排列（只读，不改动任何图标）"
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    permission: PermissionLevel = "read"

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend

    def execute(self) -> str:
        backend = _BackendCaller(self._backend or _default_backend())
        hwnd = backend.call("find_desktop_listview")
        icons = backend.call("list_icons", hwnd)
        auto = backend.call("is_auto_arrange", hwnd)
        if not icons:
            return "桌面上没有图标。"
        lines = [f"桌面图标共 {len(icons)} 个（自动排列：{'开启' if auto else '关闭'}）："]
        for icon in icons:
            lines.append(f"  [{icon['index']}] {icon['name']} @ ({icon['x']}, {icon['y']})")
        return "\n".join(lines)


class ArrangeDesktopTool(Tool):
    """按语义分组排列桌面图标位置。dry_run 默认开启，只预览方案。"""

    name = "arrange_desktop"
    description = (
        "按分组重新排列桌面图标的摆放位置（不移动文件，只改图标位置）：每组一列从左到右，"
        "组内按 icons 给定顺序从上到下，未分组的图标归入最后一列「其他」。"
        "图标名支持大小写不敏感的包含匹配（如「崩铁」可匹配「崩坏：星穹铁道」）。"
        "默认 dry_run=true 只给出排版方案；确认后再用 dry_run=false 执行。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "description": "分组列表，每组占一列从左到右排列；组内按 icons 顺序从上到下",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "分组标签（仅用于展示）"},
                        "icons": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "组内图标名（自上而下顺序），支持包含式模糊匹配",
                        },
                    },
                    "required": ["label", "icons"],
                },
            },
            "dry_run": {
                "type": "boolean",
                "description": "true（默认）只预览方案；false 才真正移动图标",
            },
        },
        "required": ["groups"],
    }
    permission: PermissionLevel = "dangerous"

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend

    def execute(self, groups: Any = None, dry_run: bool = True) -> str:
        groups = _validate_groups(groups)
        backend = _BackendCaller(self._backend or _default_backend())
        hwnd = backend.call("find_desktop_listview")
        desktop_icons = backend.call("list_icons", hwnd)
        if not desktop_icons:
            return "桌面上没有图标可排列。"

        plan, warnings = _match_icons(groups, desktop_icons)
        # 布局原点 = 现有图标包围盒左上角：多显示器下 ListView 客户端 (0,0)
        # 可能落在副屏区域，原地重排才能保证图标留在用户正在看的屏幕上
        origin_x = min(icon["x"] for icon in desktop_icons)
        origin_y = min(icon["y"] for icon in desktop_icons)
        columns = _assign_positions(plan, origin_x, origin_y)

        lines = [
            f"桌面图标排版方案（起点 ({origin_x}, {origin_y})，"
            f"列间距 {_COLUMN_WIDTH}，行间距 {_ROW_HEIGHT}）："
        ]
        for col, (label, placements) in enumerate(columns):
            lines.append(f"  [{label}] 第 {col} 列：")
            for icon, x, y in placements:
                lines.append(f"    {icon['name']} → ({x}, {y})")
        lines.extend(f"警告：{w}" for w in warnings)

        if dry_run:
            lines.append("（预览模式，未移动任何图标；确认后请用 dry_run=false 执行）")
            return "\n".join(lines)

        # 自动排列开着会立刻把图标弹回去，先关闭
        if backend.call("is_auto_arrange", hwnd):
            backend.call("set_auto_arrange", hwnd, False)
            lines.append("已关闭桌面「自动排列图标」（否则位置会被系统重置）。")

        moved = 0
        for _label, placements in columns:
            for icon, x, y in placements:
                backend.call("set_icon_position", hwnd, icon["index"], x, y)
                moved += 1
        lines.append(f"已完成排列：共移动 {moved} 个图标。")
        return "\n".join(lines)
