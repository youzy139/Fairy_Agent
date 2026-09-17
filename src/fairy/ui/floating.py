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
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
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


def radial_icon_path(name: str) -> str:
    """放射菜单按钮图标路径（radial-cmd / radial-shot / radial-organize）。"""
    return str(resources.files("fairy.ui.assets") / f"radial-{name}.png")


def app_icon_path() -> str:
    """托盘/通知用的线条版 app 图标路径。"""
    return str(resources.files("fairy.ui.assets") / "app-icon.png")


def parse_hotkey(accel: str) -> tuple[int, ...]:
    """把「ctrl+shift+space」解析为虚拟键码元组（Windows VK 码）。

    支持修饰键 ctrl/alt/shift/win 与字母、数字、space/enter/tab/esc/f1-f12。
    无法解析时抛 ValueError。
    """
    mods = {"ctrl": 0xA2, "alt": 0xA4, "shift": 0xA0, "win": 0x5B}
    specials = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B}
    specials |= {f"f{i}": 0x70 + i - 1 for i in range(1, 13)}

    parts = [p.strip().lower() for p in accel.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"热键为空：{accel!r}")
    codes: list[int] = []
    for part in parts:
        if part in mods:
            codes.append(mods[part])
        elif part in specials:
            codes.append(specials[part])
        elif len(part) == 1 and part.isalpha():
            codes.append(ord(part.upper()))
        elif len(part) == 1 and part.isdigit():
            codes.append(ord(part))
        else:
            raise ValueError(f"无法识别的热键成分 {part!r}（完整值：{accel!r}）")
    return tuple(codes)


class HotkeyManager(QObject):
    """全局热键：轮询 GetAsyncKeyState 检测组合键按下（Windows）。

    默认热键 Ctrl+Shift+Space，可用环境变量 FAIRY_HOTKEY 覆盖。
    非 Windows 平台自动禁用（no-op）。
    """

    triggered = Signal()

    def __init__(
        self,
        accel: str = "ctrl+shift+space",
        poll_ms: int = 120,
        pressed_fn: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__()
        self._codes = parse_hotkey(accel)
        self._latched = False
        if pressed_fn is not None:
            self._pressed = pressed_fn
        elif sys.platform == "win32":
            import ctypes

            get_state = ctypes.windll.user32.GetAsyncKeyState
            get_state.restype = ctypes.c_short
            self._pressed = lambda vk: bool(get_state(vk) & 0x8000)
        else:
            self._pressed = lambda vk: False  # 非 Windows：永不触发
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        pressed = all(self._pressed(vk) for vk in self._codes)
        if pressed and not self._latched:
            self._latched = True
            self.triggered.emit()
        elif not pressed:
            self._latched = False


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
    """GUI 事件总线：工具调用事件与 Agent 状态（驱动眼睛动画）。

    状态语义：idle 待机 / thinking 思考中 / working 调用工具中 /
    ok 完成闪一下 / error 出错红闪。
    """

    called = Signal(str, str)  # 工具名, 参数摘要
    stateChanged = Signal(str)  # 上述状态之一


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
    """悬浮指令条：紧凑输入 + 内联回复。

    Enter 发送，Esc 收起；回复显示在输入框下方的滚动区域（限高，
    超出可上下滚动），工具调用过程以「⚙ 工具名」形式内联提示。
    默认出现在悬浮球下方，球被拖拽时跟随移动。
    """

    WIDTH = 560
    # 回复区最大高度（像素），超出出现滚动条
    MAX_REPLY_HEIGHT = 300

    def __init__(self, components: GuiComponents) -> None:
        super().__init__()
        self._components = components
        self._worker: AgentWorker | None = None
        self._anchor: QWidget | None = None

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
        self._reply = QTextBrowser(container)
        self._reply.setOpenExternalLinks(False)
        self._reply.setStyleSheet(
            "QTextBrowser {color: rgba(220, 232, 255, 230); font-size: 13px;"
            "padding: 0px 14px; background: transparent; border: none;}"
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

    # --- 定位 ---
    def _reposition(self, anchor: QWidget) -> None:
        """贴在悬浮球下方；下方空间不足时改到上方，并保证不出屏幕。"""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = anchor.geometry().center().x() - self.width() // 2
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = anchor.y() + anchor.height() + 12
        if y + self.height() > screen.bottom() - 8:
            y = anchor.y() - self.height() - 12
        if y < screen.top() + 8:
            y = screen.top() + 8
        self.move(x, y)

    def show_near(self, anchor: QWidget) -> None:
        """在悬浮球下方弹出并聚焦输入框。"""
        self._anchor = anchor
        self._reply.hide()
        self.adjustSize()
        self._reposition(anchor)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def follow(self, anchor: QWidget) -> None:
        """悬浮球移动时跟随（仅在可见时）。"""
        self._anchor = anchor
        if self.isVisible():
            self._reposition(anchor)

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
        self._components.emitter.stateChanged.emit("thinking")

        self._worker = AgentWorker(self._components.agent, text, parent=self)
        self._worker.replyReady.connect(self._on_reply)
        self._worker.errorOccurred.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _on_tool_call(self, name: str, summary: str) -> None:
        if self._worker is not None:  # 只展示当前指令期间的工具活动
            self._set_reply(f"⚙ 正在调用工具：{name}")
            self._components.emitter.stateChanged.emit("working")

    def _on_reply(self, reply: str) -> None:
        self._set_reply(reply or "（空回复）")
        self._components.emitter.stateChanged.emit("ok")

    def _on_error(self, message: str) -> None:
        self._set_reply(f"出错了——{message}")
        self._components.emitter.stateChanged.emit("error")

    def _on_worker_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_reply(self, text: str) -> None:
        self._reply.setPlainText(text)
        self._reply.show()
        # 高度随内容增长，但不超过 MAX_REPLY_HEIGHT（超出出滚动条）
        doc = self._reply.document()
        doc.setTextWidth(self.WIDTH - 28)
        height = min(int(doc.size().height()) + 16, self.MAX_REPLY_HEIGHT)
        self._reply.setFixedHeight(height)
        self.adjustSize()
        # 高度变化后重新定位，防止下缘出屏
        if self._anchor is not None and self.isVisible():
            self._reposition(self._anchor)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


@dataclass
class RadialAction:
    """放射工具栏上的一个快捷动作。"""

    icon: str  # 图标资源名（cmd / shot / organize，见 scripts/make_icon.py）
    label: str  # 提示文字
    handler: Callable[[], None]


class _RadialButton(QPushButton):
    """放射菜单按钮：完全自绘（Qt 样式表的圆形边框在高 DPI 下会抖成白点）。

    QPainter 直接画椭圆背景 + 抗锯齿外圈 + 居中图标，任何缩放比下都平滑。
    """

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # 背景圆（悬停变亮）
        fill = QColor(50, 90, 170) if self.underMouse() else QColor(24, 48, 96)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(rect)

        # 外圈：QPainter 路径抗锯齿，不走样式表边框
        pen = painter.pen()
        pen.setColor(QColor(120, 180, 255, 220))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # 图标居中（按设备像素比取高清 pixmap，200% DPI 下不糊）
        if not self.icon().isNull():
            dpr = self.devicePixelRatioF()
            pix = self.icon().pixmap(self.iconSize() * dpr)
            pix.setDevicePixelRatio(dpr)
            target = self.rect().adjusted(
                int(self.width() * 0.21),
                int(self.height() * 0.21),
                -int(self.width() * 0.21),
                -int(self.height() * 0.21),
            )
            painter.drawPixmap(target, pix, pix.rect().toRectF().toRect())
        painter.end()


class RadialMenu(QWidget):
    """围绕悬浮球弹出的放射快捷工具栏。

    容器整体透明且鼠标穿透，只有圆形按钮响应点击；再点一下悬浮球即收起。
    按钮图标为极简白线条 PNG（scripts/make_icon.py 生成），不用 emoji。
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
            button = _RadialButton(self)
            button.setToolTip(action.label)
            button.setFixedSize(RADIAL_BUTTON_SIZE, RADIAL_BUTTON_SIZE)
            button.setIcon(QIcon(radial_icon_path(action.icon)))
            button.setIconSize(button.size() * 0.58)
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
    """蓝眼睛悬浮球：可拖拽，单击弹放射菜单，双击直达指令条，右键菜单退出。

    眼睛状态动画（借鉴「活物」思路，纯代码驱动原创图，无外部素材）：
    idle 呼吸微浮动 / thinking 缓慢旋转 / working 快速旋转 /
    ok 亮闪一下 / error 红闪，随后自动回到 idle。
    """

    STATES = ("idle", "thinking", "working", "ok", "error")
    # ok/error 状态的停留帧数（约 0.8 秒）
    _HOLD_FRAMES = 25

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_double_click: Callable[[], None] | None = None,
        on_moved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_quit = on_quit
        self._on_double_click = on_double_click
        self._on_moved = on_moved  # 拖拽移动时回调（指令条跟随）
        self._radial: RadialMenu | None = None
        self._drag_offset: Any = None
        self._dragged = False

        # 动画状态机
        self._state = "idle"
        self._angle = 0.0
        self._scale = 1.0
        self._frame = 0
        self._hold = 0

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

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_radial(self, menu: RadialMenu) -> None:
        self._radial = menu

    def toggle_visibility(self) -> None:
        """显示/隐藏悬浮球（托盘菜单用）；隐藏时顺带收起放射菜单。"""
        if self.isVisible():
            if self._radial is not None:
                self._radial.hide()
            self.hide()
        else:
            self.show()
            self.raise_()

    def set_state(self, state: str) -> None:
        """切换眼睛状态（接收 emitter.stateChanged 信号）。"""
        if state not in self.STATES or state == self._state:
            return
        self._state = state
        if state in ("ok", "error"):
            self._hold = self._HOLD_FRAMES
        if state == "idle":
            self._angle = 0.0
        self.update()

    def _tick(self) -> None:
        """动画帧推进（约 30fps）。"""
        self._frame += 1
        if self._state == "thinking":
            self._angle = (self._angle + 1.5) % 360
        elif self._state == "working":
            self._angle = (self._angle + 5.0) % 360

        if self._state == "idle":
            # 呼吸：缩放 ±3% 缓慢起伏
            self._scale = 1.0 + 0.03 * math.sin(self._frame / 8.0)
        elif self._state == "ok":
            self._scale = 1.0 + 0.06 * (self._hold / self._HOLD_FRAMES)
        else:
            self._scale = 1.0

        if self._state in ("ok", "error"):
            self._hold -= 1
            if self._hold <= 0:
                self._state = "idle"
                self._angle = 0.0
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setClipPath(path)

        # 以球心为原点做旋转/呼吸缩放
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(
            int(-self.width() / 2),
            int(-self.height() / 2),
            self.width(),
            self.height(),
            self._pixmap,
        )
        painter.resetTransform()

        # 状态色罩：error 红闪 / ok 亮闪，随剩余帧数渐隐
        if self._state in ("ok", "error") and self._hold > 0:
            alpha = int(120 * (self._hold / self._HOLD_FRAMES))
            painter.setClipPath(path)
            if self._state == "error":
                painter.fillRect(self.rect(), QColor(220, 40, 40, alpha))
            else:
                painter.fillRect(self.rect(), QColor(255, 255, 255, alpha))
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
            # 指令条跟随移动
            if self._on_moved is not None:
                self._on_moved()

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
        components.emitter.stateChanged.emit("ok")
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
        components.emitter.stateChanged.emit("error")
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


def setup_tray(app: QApplication, ball: FloatingBall, command_bar: CommandBar) -> Any:
    """系统托盘：指令入口、悬浮球显隐、退出。无可用托盘时返回 None。"""
    from PySide6.QtWidgets import QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(QIcon(app_icon_path()), app)
    menu = QMenu()
    menu.addAction("指令", lambda: command_bar.show_near(ball))
    menu.addAction("显示 / 隐藏悬浮球", ball.toggle_visibility)
    menu.addSeparator()
    menu.addAction("退出 Fairy", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Fairy Agent")
    tray.show()
    return tray


def run_gui(settings: Settings) -> int:
    """启动悬浮球 GUI。"""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # 悬浮球是唯一常驻窗口

    components = build_gui_components(settings)
    command_bar = CommandBar(components)
    ball = FloatingBall(
        on_quit=app.quit,
        on_double_click=lambda: command_bar.show_near(ball),
        on_moved=lambda: command_bar.follow(ball),
    )

    actions = [
        RadialAction("cmd", "指令", lambda: command_bar.show_near(ball)),
        RadialAction("shot", "截屏", lambda: quick_screenshot(components, ball)),
        RadialAction(
            "organize", "整理桌面", lambda: quick_organize_desktop(components, command_bar)
        ),
    ]
    ball.set_radial(RadialMenu(actions))
    # 状态总线驱动眼睛动画
    components.emitter.stateChanged.connect(ball.set_state)

    # 系统托盘与全局热键（默认 Ctrl+Shift+Space 召唤指令条）
    setup_tray(app, ball, command_bar)
    hotkey = HotkeyManager(os.environ.get("FAIRY_HOTKEY", "ctrl+shift+space"))
    hotkey.triggered.connect(lambda: command_bar.show_near(ball))
    hotkey.start()

    ball.show()
    exit_code = app.exec()
    hotkey.stop()
    if components.memory is not None:
        components.memory.close()
    return exit_code
