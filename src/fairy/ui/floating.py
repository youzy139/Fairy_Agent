"""桌面悬浮球 GUI（PySide6）。

交互设计（按用户要求：简洁、快捷、不要传统聊天窗）：

- 蓝眼睛悬浮球：无边框、置顶、可拖拽；单击弹放射快捷工具栏，双击直达指令条，
  右键菜单退出。
- 放射工具栏：围绕悬浮球的圆形按钮（指令 / 截屏 / 整理桌面）。
- 悬浮指令条：类 utools/Alfred 的紧凑输入条，输入即走，回复内联短小展示，
  Esc 收起，没有大聊天窗。
- 快捷动作：截屏直达（存「图片/屏幕截图」，Toast 可点击打开）；
  整理桌面改为给 Agent 下达语义摆位指令（图标不移动文件，只重排位置）。
- 安全模型与 CLI 一致：write/dangerous 级操作经 _ConfirmBridge 弹窗确认；
  所有快捷动作写审计日志。
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fairy.agent.core import Agent
from fairy.config import Settings
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import PolicyEngine
from fairy.tools.registry import ToolRegistry
from fairy.tools.screenshot import ScreenshotTool
from fairy.ui.cli import build_registry

# 悬浮球直径（像素）
BALL_SIZE = 72
# 放射工具栏：按钮直径与围绕球的半径
RADIAL_BUTTON_SIZE = 46
RADIAL_RADIUS = 82

# 快捷截屏的保存目录（系统「图片/屏幕截图」，与 Windows 截图习惯一致）
QUICK_SCREENSHOT_DIR = Path.home() / "Pictures" / "Screenshots"

# 「整理桌面」快捷动作下达给 Agent 的指令
ORGANIZE_INSTRUCTION = (
    "帮我整理桌面图标的摆放：先用 list_desktop_icons 列出桌面图标，"
    "按软件属性语义分组（比如游戏、开发工具、办公软件、系统工具），"
    "同一公司或同一系列的排在一起（例如米哈游的游戏相邻），"
    "然后用 arrange_desktop 先 dry_run 给我排版方案；我确认后你再正式执行。"
)


def eye_image_path() -> str:
    """蓝眼睛图片路径（包内资源，PyInstaller 打包后同样可用）。"""
    return str(resources.files("fairy.ui.assets") / "eye.png")


class _PendingCall:
    """一次跨线程确认调用的结果容器。"""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None


class _ConfirmBridge(QObject):
    """把 policy 的确认回调桥接到 GUI 线程弹窗。

    工作线程 emit 信号后阻塞在 ``_PendingCall.event`` 上；
    GUI 线程的槽函数弹窗、写入结果并唤醒工作线程。
    """

    confirmRequested = Signal(str, object)
    phraseRequested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parent = parent
        self.confirmRequested.connect(self._on_confirm)
        self.phraseRequested.connect(self._on_phrase)

    def _on_confirm(self, prompt: str, call: _PendingCall) -> None:
        answer = QMessageBox.question(
            self._parent,
            "Fairy 需要确认",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        call.value = answer == QMessageBox.StandardButton.Yes
        call.event.set()

    def _on_phrase(self, prompt: str, call: _PendingCall) -> None:
        text, ok = QInputDialog.getText(self._parent, "Fairy 二次确认", prompt)
        call.value = text if ok else ""
        call.event.set()

    # 以下两个回调在工作线程中执行
    def confirm(self, prompt: str) -> bool:
        call = _PendingCall()
        self.confirmRequested.emit(prompt, call)
        call.event.wait()
        return bool(call.value)

    def confirm_phrase(self, prompt: str) -> str:
        call = _PendingCall()
        self.phraseRequested.emit(prompt, call)
        call.event.wait()
        return str(call.value or "")


class _ToolCallEmitter(QObject):
    """把 Agent 的工具调用事件转成 Qt 信号（跨线程自动排队到 GUI 线程）。"""

    called = Signal(str, str)  # 工具名, 参数摘要


class FuncWorker(QThread):
    """后台线程执行一个普通函数（截屏等），避免阻塞 GUI。"""

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class AgentWorker(QThread):
    """后台线程执行一轮 Agent 对话，避免阻塞 GUI。"""

    replyReady = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, agent: Agent, user_text: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._agent = agent
        self._user_text = user_text

    def run(self) -> None:
        try:
            reply = self._agent.chat(self._user_text)
        except Exception as exc:  # LLM 错误等，反馈到界面
            self.errorOccurred.emit(str(exc))
            return
        self.replyReady.emit(reply)


class Toast(QLabel):
    """悬浮球旁的轻提示，几秒后自动消失；支持点击查看（如打开截图所在目录）。"""

    def __init__(
        self,
        text: str,
        anchor: QWidget,
        on_click: Callable[[], None] | None = None,
        duration_ms: int = 5000,
    ) -> None:
        super().__init__(text)
        self._on_click = on_click
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        style = (
            "background: rgba(20, 40, 80, 220); color: white; padding: 8px 12px;"
            "border-radius: 8px; font-size: 13px;"
        )
        if on_click is not None:
            style += "text-decoration: underline;"
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(style)
        self.adjustSize()
        # 显示在悬浮球左侧，避免遮挡
        self.move(anchor.x() - self.width() - 12, anchor.y() + BALL_SIZE // 3)
        self.show()
        QTimer.singleShot(duration_ms, self.close)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._on_click is not None:
            self._on_click()
        self.close()


class CommandBar(QWidget):
    """悬浮指令条：紧凑输入 + 内联短回复。

    Enter 发送，Esc 收起；回复显示在输入框下方的小区域里，
    工具调用过程以「⚙ 工具名」形式内联提示。
    """

    WIDTH = 560

    def __init__(self, components: GuiComponents) -> None:
        super().__init__()
        self._components = components
        self._worker: AgentWorker | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        container = QFrame(self)
        container.setStyleSheet(
            "QFrame {background: rgba(14, 24, 48, 240); border-radius: 12px;"
            "border: 1px solid rgba(120, 180, 255, 120);}"
        )
        self._input = QLineEdit(container)
        self._input.setPlaceholderText("告诉 Fairy 要做什么…（Enter 执行，Esc 收起）")
        self._input.setStyleSheet(
            "QLineEdit {background: transparent; border: none; color: white;"
            "font-size: 15px; padding: 10px 14px;}"
        )
        self._reply = QLabel(container)
        self._reply.setWordWrap(True)
        self._reply.setStyleSheet(
            "QLabel {color: rgba(220, 232, 255, 230); font-size: 13px;"
            "padding: 0px 14px 10px 14px; background: transparent; border: none;}"
        )
        self._reply.hide()

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._input)
        layout.addWidget(self._reply)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        self.setFixedWidth(self.WIDTH)
        self._input.returnPressed.connect(self._send)
        components.emitter.called.connect(self._on_tool_call)

    # --- 对外接口 ---
    def show_near(self, anchor: QWidget) -> None:
        """在悬浮球上方弹出并聚焦输入框。"""
        self._reply.hide()
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = anchor.geometry().center().x() - self.width() // 2
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = anchor.y() - self.height() - 12
        if y < screen.top() + 8:  # 球太靠上时改到下方
            y = anchor.y() + anchor.height() + 12
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def run_command(self, text: str) -> None:
        """以外部指令驱动（如快捷动作「整理桌面」）：弹出并直接发送。"""
        self._input.setText(text)
        if not self.isVisible():
            # 无锚点时贴屏幕底部中央
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.adjustSize()
            self.move(
                screen.center().x() - self.width() // 2,
                screen.bottom() - self.height() - 80,
            )
            self.show()
            self.raise_()
            self.activateWindow()
        self._send()

    # --- 内部逻辑 ---
    def _send(self) -> None:
        text = self._input.text().strip()
        if not text or self._worker is not None:
            return
        self._input.clear()
        self._set_reply("思考中…")

        self._worker = AgentWorker(self._components.agent, text, parent=self)
        self._worker.replyReady.connect(self._on_reply)
        self._worker.errorOccurred.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _on_tool_call(self, name: str, summary: str) -> None:
        if self._worker is not None:  # 只展示当前指令期间的工具活动
            self._set_reply(f"⚙ 正在调用工具：{name}")

    def _on_reply(self, reply: str) -> None:
        self._set_reply(reply or "（空回复）")

    def _on_error(self, message: str) -> None:
        self._set_reply(f"出错了——{message}")

    def _on_worker_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_reply(self, text: str) -> None:
        self._reply.setText(text)
        self._reply.show()
        self.adjustSize()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


@dataclass
class RadialAction:
    """放射工具栏上的一个快捷动作。"""

    icon: str  # 按钮上的图标文字（emoji）
    label: str  # 提示文字
    handler: Callable[[], None]


class RadialMenu(QWidget):
    """围绕悬浮球弹出的放射快捷工具栏。

    容器整体透明且鼠标穿透，只有圆形按钮响应点击；再点一下悬浮球即收起。
    """

    def __init__(self, actions: list[RadialAction]) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 空白区域鼠标穿透，事件直达下层窗口/桌面
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        size = (RADIAL_RADIUS + RADIAL_BUTTON_SIZE) * 2
        self.setFixedSize(size, size)

        self._buttons: list[QPushButton] = []
        count = len(actions)
        for i, action in enumerate(actions):
            button = QPushButton(action.icon, self)
            button.setToolTip(action.label)
            button.setFixedSize(RADIAL_BUTTON_SIZE, RADIAL_BUTTON_SIZE)
            button.setStyleSheet(
                "QPushButton {background: rgba(24, 48, 96, 235); color: white;"
                f"border-radius: {RADIAL_BUTTON_SIZE // 2}px; font-size: 20px;"
                "border: 1px solid rgba(120, 180, 255, 160);}"
                "QPushButton:hover {background: rgba(50, 90, 170, 255);}"
            )
            button.clicked.connect(action.handler)
            # 沿上半圆弧均匀分布（球常在屏幕下缘，按钮向上展开）
            angle = math.radians(150 - i * (120 / max(count - 1, 1)))
            cx = size // 2 + int(RADIAL_RADIUS * math.cos(angle))
            cy = size // 2 - int(RADIAL_RADIUS * math.sin(angle))
            button.move(cx - RADIAL_BUTTON_SIZE // 2, cy - RADIAL_BUTTON_SIZE // 2)
            self._buttons.append(button)

    @property
    def buttons(self) -> list[QPushButton]:
        return self._buttons

    def toggle(self, ball: QWidget) -> None:
        """以悬浮球为中心弹出/收起。"""
        if self.isVisible():
            self.hide()
            return
        center = ball.geometry().center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self.show()
        self.raise_()


class FloatingBall(QWidget):
    """蓝眼睛悬浮球：可拖拽，单击弹放射菜单，双击直达指令条，右键菜单退出。"""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_double_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_quit = on_quit
        self._on_double_click = on_double_click
        self._radial: RadialMenu | None = None
        self._drag_offset: Any = None
        self._dragged = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 不出现在任务栏
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(BALL_SIZE, BALL_SIZE)

        self._pixmap = QPixmap(eye_image_path())

        # 初始位置：屏幕右下角偏上
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - BALL_SIZE - 40, screen.bottom() - BALL_SIZE - 120)

    def set_radial(self, menu: RadialMenu) -> None:
        self._radial = menu

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setClipPath(path)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.end()

    # --- 拖拽与点击 ---
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._dragged = True
            # 拖拽时收起放射菜单，避免按钮悬空
            if self._radial is not None:
                self._radial.hide()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragged and self._radial is not None:
                self._radial.toggle(self)
            self._drag_offset = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._radial is not None:
                self._radial.hide()
            if self._on_double_click is not None:
                self._on_double_click()

    def contextMenuEvent(self, event: Any) -> None:
        menu = QMenu(self)
        quit_action = menu.addAction("退出 Fairy")
        if menu.exec(event.globalPos()) is quit_action:
            self._on_quit()


@dataclass
class GuiComponents:
    """GUI 组装产物：Agent 与其安全/审计/记忆依赖、工作区、事件发射器。"""

    agent: Agent
    audit: AuditLogger
    bridge: _ConfirmBridge
    registry: ToolRegistry
    workspace: str
    emitter: _ToolCallEmitter
    memory: Any = None  # MemoryStore | None（memory_backend=sqlite 时启用）


def build_gui_components(settings: Settings) -> GuiComponents:
    """组装 GUI 版 Agent 及依赖：确认回调走 _ConfirmBridge 弹窗。"""
    from fairy.llm.client import LLMClient
    from fairy.memory.store import MemoryStore

    workspace = os.getcwd()
    bridge = _ConfirmBridge(None)
    policy = PolicyEngine(
        confirm=bridge.confirm,
        confirm_phrase=bridge.confirm_phrase,
        confirm_dangerous=settings.confirm_dangerous,
    )
    audit = AuditLogger(settings.data_dir)
    emitter = _ToolCallEmitter()

    # 记忆层：sqlite 后端时每次启动新建会话并持久化消息
    memory: MemoryStore | None = None
    session_id: int | None = None
    if settings.memory_backend == "sqlite":
        memory = MemoryStore(settings.data_dir)
        session_id = memory.create_session(workspace)

    registry = build_registry(settings, workspace, memory=memory)

    def on_tool_call(name: str, args: dict[str, Any]) -> None:
        # 工作线程回调：转成 Qt 信号交给指令条展示
        summary = json.dumps(args, ensure_ascii=False)[:200]
        emitter.called.emit(name, summary)

    agent = Agent(
        settings=settings,
        llm_client=LLMClient(settings),
        registry=registry,
        policy=policy,
        audit=audit,
        on_tool_call=on_tool_call,
        memory=memory,
        session_id=session_id,
    )
    return GuiComponents(
        agent=agent,
        audit=audit,
        bridge=bridge,
        registry=registry,
        workspace=workspace,
        emitter=emitter,
        memory=memory,
    )


def quick_screenshot(components: GuiComponents, anchor: QWidget) -> None:
    """快捷动作：截屏直达，存「图片/屏幕截图」，Toast 点击可打开所在目录。"""
    tool = ScreenshotTool(components.workspace, output_dir=QUICK_SCREENSHOT_DIR)

    worker = FuncWorker(tool.execute, parent=anchor)
    anchor._workers = getattr(anchor, "_workers", []) + [worker]  # 防止提前回收

    def on_done(message: str) -> None:
        components.audit.log(
            tool="screenshot",
            args={"source": "quick_action"},
            allowed=True,
            result_summary=message,
        )
        # 消息格式：截屏已保存：<path>（WxH）——提取路径用于点击打开
        path = message.split("：", 1)[-1].split("（")[0]

        # 顺手复制到剪贴板：截屏最常见的下一步就是 Ctrl+V 粘贴
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            QGuiApplication.clipboard().setPixmap(pixmap)
            copied = True
        else:
            copied = False

        def open_folder() -> None:
            # 本机资源管理器打开截图所在目录（固定系统程序，无注入面）
            os.startfile(str(Path(path).parent))

        note = "已复制，可直接 Ctrl+V（点击查看文件）" if copied else "已保存（点击查看）"
        Toast(f"{Path(path).name} {note}", anchor, on_click=open_folder)

    def on_failed(message: str) -> None:
        components.audit.log(
            tool="screenshot",
            args={"source": "quick_action"},
            allowed=True,
            result_summary=f"执行失败：{message}",
        )
        Toast(f"截屏失败：{message}", anchor)

    worker.done.connect(on_done)
    worker.failed.connect(on_failed)
    worker.start()


def quick_organize_desktop(components: GuiComponents, command_bar: CommandBar) -> None:
    """快捷动作：整理桌面——向 Agent 下达语义摆位指令。

    Agent 会先 list_desktop_icons，再 arrange_desktop（dry_run 出方案，
    经弹窗确认 + 确认词后才真正摆位）。全程走 PolicyEngine 与审计。
    """
    command_bar.run_command(ORGANIZE_INSTRUCTION)


def run_gui(settings: Settings) -> int:
    """启动悬浮球 GUI。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 悬浮球是唯一常驻窗口

    components = build_gui_components(settings)
    command_bar = CommandBar(components)
    ball = FloatingBall(
        on_quit=app.quit,
        on_double_click=lambda: command_bar.show_near(ball),
    )

    actions = [
        RadialAction("💬", "指令", lambda: command_bar.show_near(ball)),
        RadialAction("📷", "截屏", lambda: quick_screenshot(components, ball)),
        RadialAction("🗂", "整理桌面", lambda: quick_organize_desktop(components, command_bar)),
    ]
    ball.set_radial(RadialMenu(actions))

    ball.show()
    exit_code = app.exec()
    if components.memory is not None:
        components.memory.close()
    return exit_code
