"""记忆工具：remember（write 级，但仅写 Fairy 自身记忆库，auto_allow 恒真）。

用户对话教学的入口：「记住 B 站是 bilibili.com」「以后鸣潮就指这个路径」。
别名/收藏存入记忆层 preferences 表：

- ``alias:<名字>`` → 应用路径（open_app 消费）
- ``fav:<名字>``   → 网址（browser_open 消费）
- ``pref:<名字>``  → 任意用户偏好
"""

from __future__ import annotations

from typing import Any

from fairy.tools.base import PermissionLevel, Tool, ToolError

_KIND_PREFIX = {
    "app": "alias:",
    "site": "fav:",
    "preference": "pref:",
}


class RememberTool(Tool):
    """记住别名、收藏网址或用户偏好，持久化到记忆层。"""

    name = "remember"
    description = (
        "记住一个别名/收藏/偏好：kind=app 记住应用路径（供 open_app 使用）；"
        "kind=site 记住网址（供 browser_open 使用）；kind=preference 记住任意用户偏好。"
        "例如用户说「记住 B 站是 bilibili.com」→ kind=site, name=B站, target=https://www.bilibili.com"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["app", "site", "preference"],
                "description": "记忆类型：app 应用别名 / site 网址收藏 / preference 用户偏好",
            },
            "name": {"type": "string", "description": "名字，如「B站」「鸣潮」"},
            "target": {"type": "string", "description": "目标：路径、网址或偏好内容"},
        },
        "required": ["kind", "name", "target"],
    }
    permission: PermissionLevel = "write"

    def __init__(self, memory: Any = None) -> None:
        # memory 为 MemoryStore；None 时执行报错提示
        self._memory = memory

    def auto_allow(self, args: dict[str, Any]) -> bool:
        # 只写 Fairy 自身记忆库，不涉及用户数据，免确认
        return True

    def execute(self, kind: str, name: str, target: str) -> str:
        if self._memory is None:
            raise ToolError("记忆层未启用（FAIRY_MEMORY_BACKEND=sqlite 时可用）。")
        prefix = _KIND_PREFIX.get(kind)
        if prefix is None:
            raise ToolError(f"未知记忆类型 {kind!r}，可选：app / site / preference。")
        name = name.strip()
        target = target.strip()
        if not name or not target:
            raise ToolError("name 和 target 不能为空。")
        self._memory.set_preference(prefix + name.lower(), target)
        kind_label = {"app": "应用别名", "site": "网址收藏", "preference": "用户偏好"}[kind]
        return f"已记住{kind_label}：{name} → {target}"
