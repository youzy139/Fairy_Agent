"""fairy/ui/floating.py 单元测试（离屏模式，不弹真实窗口）。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

# 必须在导入 PySide6 之前设置离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fairy.ui.floating import BALL_SIZE, ChatWindow, FloatingBall, eye_image_path  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _stub_agent(reply: str = "收到") -> SimpleNamespace:
    """最小 Agent 桩：duck typing 满足 AgentWorker 的调用。"""
    return SimpleNamespace(chat=lambda text: f"{reply}:{text}")


def test_eye_image_exists() -> None:
    assert os.path.isfile(eye_image_path())


def test_floating_ball_creation(qapp) -> None:
    chat = ChatWindow(_stub_agent())
    ball = FloatingBall(chat, on_quit=lambda: None)
    assert ball.width() == BALL_SIZE
    assert ball.height() == BALL_SIZE
    assert not ball._pixmap.isNull()
    # 初始时对话窗口隐藏
    assert not chat.isVisible()


def test_ball_click_toggles_chat(qapp) -> None:
    chat = ChatWindow(_stub_agent())
    ball = FloatingBall(chat, on_quit=lambda: None)
    ball.show()
    assert not chat.isVisible()

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert chat.isVisible()

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert not chat.isVisible()


def test_chat_window_send_and_reply(qapp) -> None:
    win = ChatWindow(_stub_agent("回复"))
    win._input.setText("你好")
    win._send()

    # 等待后台线程完成（离屏模式下事件循环需手动驱动）
    for _ in range(50):
        QTest.qWait(20)
        if win._worker is None:
            break

    text = win._history.toPlainText()
    assert "你好" in text
    assert "回复:你好" in text


def test_chat_window_empty_input_ignored(qapp) -> None:
    win = ChatWindow(_stub_agent())
    win._input.setText("   ")
    win._send()
    assert win._worker is None


def test_chat_window_close_hides_instead_of_quit(qapp) -> None:
    win = ChatWindow(_stub_agent())
    win.show()
    win.close()
    # closeEvent 被忽略：窗口只是隐藏，应用不退出
    assert not win.isVisible()
