"""桌面悬浮球 GUI（PySide6）。

- 蓝眼睛悬浮球：无边框、置顶、可拖拽；单击弹出放射快捷工具栏，双击直达对话窗口，
  右键菜单退出。
- 放射工具栏：围绕悬浮球弹出圆形按钮（聊天 / 截屏 / 整理桌面），快捷动作直接调用
  对应工具；截屏异步执行，整理桌面先出方案、经确认后才移动文件。
- 对话窗口：复用 Agent Core；LLM 调用在后台线程执行，界面不冻结。
- 安全模型与 CLI 一致：write 级操作弹窗确认一次，dangerous 级需输入确认词，
  确认回调通过跨线程信号桥接到 GUI 线程弹窗，工作线程阻塞等待结果。
- 快捷动作（不经过 LLM 的直接工具调用）同样写入审计日志。
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from fairy.agent.core import Agent
from fairy.config import Settings
from fairy.safety.audit import AuditLogger
from fairy.safety.policy import CONFIRM_PHRASE, PolicyEngine
from fairy.tools.base import ToolError
from fairy.tools.registry import ToolRegistry
from fairy.ui.cli import build_registry

# 悬浮球直径（像素）
BALL_SIZE = 72
# 放射工具栏：按钮直径与围绕球的半径
RADIAL_BUTTON_SIZE = 46
RADIAL_RADIUS = 82


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


class FuncWorker(QThread):
    """后台线程执行一个普通函数（截屏、整理桌面等），避免阻塞 GUI。"""

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
    toolCalled = Signal(str, str)  # 工具名, 参数摘要
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
    """悬浮球旁的轻提示，几秒后自动消失。"""

    def __init__(self, text: str, anchor: QWidget) -> None:
        super().__init__(text)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(
            "background: rgba(20, 40, 80, 220); color: white; padding: 8px 12px;"
            "border-radius: 8px; font-size: 13px;"
        )
        self.adjustSize()
        # 显示在悬浮球左侧，避免遮挡
        self.move(anchor.x() - self.width() - 12, anchor.y() + BALL_SIZE // 3)
        self.show()
        QTimer.singleShot(3000, self.close)


class ChatWindow(QWidget):
    """对话窗口：消息历史 + 输入框，隐藏而非关闭（由悬浮球切换）。"""

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self._agent = agent
        self._worker: AgentWorker | None = None

        self.setWindowTitle("Fairy")
        self.resize(440, 540)

        self._history = QTextBrowser(self)
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("和 Fairy 说点什么…（Enter 发送）")
        self._send_btn = QPushButton("发送", self)

        bottom = QHBoxLayout()
        bottom.addWidget(self._input, stretch=1)
        bottom.addWidget(self._send_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self._history, stretch=1)
        layout.addLayout(bottom)

        self._send_btn.clicked.connect(self._send)
        self._input.returnPressed.connect(self._send)

        self._append("Fairy", "你好，我是 Fairy。点我弹快捷菜单，双击直接对话。")

    def _append(self, who: str, text: str) -> None:
        self._history.append(f"<b>{who}:</b> {text}")

    def _send(self) -> None:
        text = self._input.text().strip()
        if not text or self._worker is not None:
            return
        self._input.clear()
        self._append("你", text)
        self._set_busy(True)

        self._worker = AgentWorker(self._agent, text, parent=self)
        self._worker.replyReady.connect(self._on_reply)
        self._worker.errorOccurred.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _on_reply(self, reply: str) -> None:
        self._append("Fairy", reply or "（空回复）")

    def _on_error(self, message: str) -> None:
        self._append("Fairy", f"出错了——{message}")

    def _on_worker_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._input.setDisabled(busy)
        self._send_btn.setDisabled(busy)

    def toggle_visibility(self) -> None:
        """显示/隐藏切换（悬浮球双击或快捷菜单「聊天」时调用）。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event: Any) -> None:  # 关闭按钮只隐藏，不退出应用
        self.hide()
        event.ignore()


@dataclass
class RadialAction:
    """放射工具栏上的一个快捷动作。"""

    icon: str  # 按钮上的图标文字（emoji）
    label: str  # 提示文字
    handler: Callable[[], None]


class RadialMenu(QWidget):
    """围绕悬浮球弹出的放射快捷工具栏。

    容器整体透明且鼠标穿透，只有圆形按钮响应点击；再点一下悬浮球或点击
    空白处即收起。
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
    """蓝眼睛悬浮球：可拖拽，单击弹放射菜单，双击直达对话，右键菜单退出。"""

    def __init__(self, chat_window: ChatWindow, on_quit: Any) -> None:
        super().__init__()
        self._chat_window = chat_window
        self._on_quit = on_quit
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
            if not self._dragged:
                if self._radial is not None:
                    self._radial.toggle(self)
                else:
                    self._chat_window.toggle_visibility()
            self._drag_offset = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._radial is not None:
                self._radial.hide()
            self._chat_window.toggle_visibility()

    def contextMenuEvent(self, event: Any) -> None:
        menu = QMenu(self)
        toggle_action = menu.addAction("显示 / 隐藏对话")
        quit_action = menu.addAction("退出 Fairy")
        action = menu.exec(event.globalPos())
        if action is toggle_action:
            self._chat_window.toggle_visibility()
        elif action is quit_action:
            self._on_quit()


@dataclass
class GuiComponents:
    """GUI 组装产物：Agent 与其安全/审计/记忆依赖、工作区。"""

    agent: Agent
    audit: AuditLogger
    bridge: _ConfirmBridge
    registry: ToolRegistry
    workspace: str
    memory: Any = None  # MemoryStore | None（memory_backend=sqlite 时启用）


def build_gui_components(settings: Settings) -> GuiComponents:
    """组装 GUI 版 Agent 及依赖：确认回调走 _ConfirmBridge 弹窗。"""
    from fairy.llm.client import LLMClient
    from fairy.memory.store import MemoryStore

    workspace = os.getcwd()
    registry = build_registry(settings, workspace)
    bridge = _ConfirmBridge(None)
    policy = PolicyEngine(
        confirm=bridge.confirm,
        confirm_phrase=bridge.confirm_phrase,
        confirm_dangerous=settings.confirm_dangerous,
    )
    audit = AuditLogger(settings.data_dir)

    # 记忆层：sqlite 后端时每次启动新建会话并持久化消息
    memory: MemoryStore | None = None
    session_id: int | None = None
    if settings.memory_backend == "sqlite":
        memory = MemoryStore(settings.data_dir)
        session_id = memory.create_session(workspace)

    def on_tool_call(name: str, args: dict[str, Any]) -> None:
        # 工作线程回调：目前仅记录到日志，界面通过回复感知
        summary = json.dumps(args, ensure_ascii=False)[:200]
        print(f"[工具调用] {name} {summary}")

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
        memory=memory,
    )


def quick_screenshot(components: GuiComponents, anchor: QWidget) -> None:
    """快捷动作：截屏（read 级，直接执行，结果写审计并弹 Toast）。"""
    tool = components.registry.get("screenshot")
    if tool is None:
        return

    worker = FuncWorker(tool.execute, parent=anchor)
    anchor._workers = getattr(anchor, "_workers", []) + [worker]  # 防止提前回收

    def on_done(message: str) -> None:
        components.audit.log(
            tool="screenshot",
            args={"source": "quick_action"},
            allowed=True,
            result_summary=message,
        )
        Toast(message, anchor)

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


def quick_organize_desktop(components: GuiComponents, anchor: QWidget) -> None:
    """快捷动作：整理桌面（dangerous 级）。

    流程：dry_run 出方案 → 弹窗确认 → 输入确认词「确认执行」→ 后台执行。
    每一步都写审计日志，与 CLI/LLM 路径的安全要求一致。
    """
    tool = components.registry.get("organize_desktop")
    if tool is None:
        return

    try:
        plan = tool.execute(dry_run=True)
    except ToolError as exc:
        components.audit.log(
            tool="organize_desktop",
            args={"dry_run": True, "source": "quick_action"},
            allowed=True,
            result_summary=f"执行失败：{exc}",
        )
        Toast(f"无法整理桌面：{exc}", anchor)
        return
    components.audit.log(
        tool="organize_desktop",
        args={"dry_run": True, "source": "quick_action"},
        allowed=True,
        result_summary=plan,
    )

    box = QMessageBox(anchor)
    box.setWindowTitle("Fairy · 整理桌面")
    box.setText("整理方案如下，是否执行？")
    box.setDetailedText(plan)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    if box.exec() != QMessageBox.StandardButton.Yes:
        components.audit.log(
            tool="organize_desktop",
            args={"dry_run": False, "source": "quick_action"},
            allowed=False,
            deny_reason="用户在方案确认环节取消。",
        )
        return

    phrase, ok = QInputDialog.getText(
        anchor, "Fairy 二次确认", f"请输入「{CONFIRM_PHRASE}」以执行桌面整理："
    )
    if not ok or phrase.strip() != CONFIRM_PHRASE:
        components.audit.log(
            tool="organize_desktop",
            args={"dry_run": False, "source": "quick_action"},
            allowed=False,
            deny_reason="二次确认词不正确，已取消执行。",
        )
        Toast("已取消整理桌面。", anchor)
        return

    worker = FuncWorker(lambda: tool.execute(dry_run=False), parent=anchor)
    anchor._workers = getattr(anchor, "_workers", []) + [worker]

    def on_done(message: str) -> None:
        components.audit.log(
            tool="organize_desktop",
            args={"dry_run": False, "source": "quick_action"},
            allowed=True,
            result_summary=message,
        )
        Toast("桌面整理完成。", anchor)

    worker.done.connect(on_done)
    worker.start()


def run_gui(settings: Settings) -> int:
    """启动悬浮球 GUI。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 关掉对话窗口不退出，由悬浮球控制

    components = build_gui_components(settings)
    chat = ChatWindow(components.agent)
    ball = FloatingBall(chat, on_quit=app.quit)

    actions = [
        RadialAction("💬", "聊天", chat.toggle_visibility),
        RadialAction("📷", "截屏", lambda: quick_screenshot(components, ball)),
        RadialAction("🗂", "整理桌面", lambda: quick_organize_desktop(components, ball)),
    ]
    ball.set_radial(RadialMenu(actions))

    ball.show()
    exit_code = app.exec()
    if components.memory is not None:
        components.memory.close()
    return exit_code
