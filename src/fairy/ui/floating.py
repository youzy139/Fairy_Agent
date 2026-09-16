"""桌面悬浮球 GUI（PySide6）。

- 蓝眼睛悬浮球：无边框、置顶、可拖拽，点击弹出/收起对话窗口，右键菜单退出。
- 对话窗口：复用 Agent Core；LLM 调用在后台线程执行，界面不冻结。
- 安全模型与 CLI 一致：write 级操作弹窗确认一次，dangerous 级需输入确认词，
  确认回调通过跨线程信号桥接到 GUI 线程弹窗，工作线程阻塞等待结果。
"""

from __future__ import annotations

import json
import os
import threading
from importlib import resources
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
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
from fairy.safety.policy import PolicyEngine
from fairy.ui.cli import build_registry

# 悬浮球直径（像素）
BALL_SIZE = 72


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

        self._append("Fairy", "你好，我是 Fairy。点击悬浮球可以收起我。")

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
        """显示/隐藏切换（悬浮球点击时调用）。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event: Any) -> None:  # 关闭按钮只隐藏，不退出应用
        self.hide()
        event.ignore()


class FloatingBall(QWidget):
    """蓝眼睛悬浮球：可拖拽，单击切换对话窗口，右键菜单退出。"""

    def __init__(self, chat_window: ChatWindow, on_quit: Any) -> None:
        super().__init__()
        self._chat_window = chat_window
        self._on_quit = on_quit
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

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragged:
                self._chat_window.toggle_visibility()
            self._drag_offset = None

    def contextMenuEvent(self, event: Any) -> None:
        menu = QMenu(self)
        toggle_action = menu.addAction("显示 / 隐藏对话")
        quit_action = menu.addAction("退出 Fairy")
        action = menu.exec(event.globalPos())
        if action is toggle_action:
            self._chat_window.toggle_visibility()
        elif action is quit_action:
            self._on_quit()


def build_agent_gui(settings: Settings, chat_parent: QWidget | None = None) -> Agent:
    """组装 GUI 版 Agent：确认回调走 _ConfirmBridge 弹窗。"""
    from fairy.llm.client import LLMClient

    workspace = os.getcwd()
    registry = build_registry(settings, workspace)
    bridge = _ConfirmBridge(chat_parent)
    policy = PolicyEngine(
        confirm=bridge.confirm,
        confirm_phrase=bridge.confirm_phrase,
        confirm_dangerous=settings.confirm_dangerous,
    )
    audit = AuditLogger(settings.data_dir)

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
    )
    # bridge 需与 agent 同生命周期，避免被垃圾回收
    agent._gui_bridge = bridge  # type: ignore[attr-defined]
    return agent


def run_gui(settings: Settings) -> int:
    """启动悬浮球 GUI。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 关掉对话窗口不退出，由悬浮球控制

    agent = build_agent_gui(settings)
    chat = ChatWindow(agent)
    ball = FloatingBall(chat, on_quit=app.quit)
    ball.show()

    return app.exec()
