"""fairy/ui/floating.py 单元测试（离屏模式，不弹真实窗口）。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

# 必须在导入 PySide6 之前设置离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from fairy.safety.audit import AuditLogger  # noqa: E402
from fairy.safety.policy import CONFIRM_PHRASE  # noqa: E402
from fairy.tools.base import Tool  # noqa: E402
from fairy.tools.registry import ToolRegistry  # noqa: E402
from fairy.ui.floating import (  # noqa: E402
    BALL_SIZE,
    ChatWindow,
    FloatingBall,
    GuiComponents,
    RadialAction,
    RadialMenu,
    eye_image_path,
    quick_organize_desktop,
    quick_screenshot,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _stub_agent(reply: str = "收到") -> SimpleNamespace:
    """最小 Agent 桩：duck typing 满足 AgentWorker 的调用。"""
    return SimpleNamespace(chat=lambda text: f"{reply}:{text}")


def _make_ball(qapp, with_radial: bool = True):
    """造一个聊天窗 + 悬浮球（可选放射菜单）。"""
    chat = ChatWindow(_stub_agent())
    ball = FloatingBall(chat, on_quit=lambda: None)
    menu = None
    if with_radial:
        calls: list[str] = []
        actions = [
            RadialAction("💬", "聊天", chat.toggle_visibility),
            RadialAction("📷", "截屏", lambda: calls.append("shot")),
            RadialAction("🗂", "整理桌面", lambda: calls.append("organize")),
        ]
        menu = RadialMenu(actions)
        ball.set_radial(menu)
    return chat, ball, menu


def test_eye_image_exists() -> None:
    assert os.path.isfile(eye_image_path())


def test_floating_ball_creation(qapp) -> None:
    chat, ball, _ = _make_ball(qapp, with_radial=False)
    assert ball.width() == BALL_SIZE
    assert ball.height() == BALL_SIZE
    assert not ball._pixmap.isNull()
    assert not chat.isVisible()


def test_ball_click_toggles_radial_menu(qapp) -> None:
    chat, ball, menu = _make_ball(qapp)
    ball.show()
    assert not menu.isVisible()
    assert not chat.isVisible()

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert menu.isVisible()
    assert not chat.isVisible()  # 单击不再直接开聊天窗

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert not menu.isVisible()


def test_ball_double_click_toggles_chat(qapp) -> None:
    chat, ball, menu = _make_ball(qapp)
    ball.show()

    QTest.mouseDClick(ball, Qt.MouseButton.LeftButton)
    assert chat.isVisible()
    assert not menu.isVisible()

    QTest.mouseDClick(ball, Qt.MouseButton.LeftButton)
    assert not chat.isVisible()


def test_radial_menu_buttons_and_actions(qapp) -> None:
    chat, ball, menu = _make_ball(qapp)
    assert len(menu.buttons) == 3
    # 「聊天」按钮切换对话窗口
    menu.buttons[0].click()
    assert chat.isVisible()
    menu.buttons[0].click()
    assert not chat.isVisible()


def test_radial_menu_positions_around_ball(qapp) -> None:
    chat, ball, menu = _make_ball(qapp)
    ball.show()
    menu.toggle(ball)
    # 菜单中心与球心重合（离屏平台的窗口管理器有少量取整误差，容差 2px）
    delta = menu.geometry().center() - ball.geometry().center()
    assert abs(delta.x()) <= 2 and abs(delta.y()) <= 2


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


class _StubTool(Tool):
    """记录调用参数的最小工具桩。"""

    name = "stub"
    description = "stub"
    parameters: dict = {"type": "object", "properties": {}}
    permission = "read"

    def __init__(self, result: str = "完成") -> None:
        self.calls: list[dict] = []
        self._result = result

    def execute(self, **args) -> str:
        self.calls.append(args)
        return self._result


def _components(tmp_path: Path, tool: Tool, name: str) -> GuiComponents:
    registry = ToolRegistry()
    tool.name = name
    registry.register(tool)
    return GuiComponents(
        agent=None,  # 快捷动作不经过 Agent
        audit=AuditLogger(tmp_path / "data"),
        bridge=None,
        registry=registry,
        workspace=str(tmp_path),
    )


def _wait_workers(widget, rounds: int = 100) -> None:
    for _ in range(rounds):
        QTest.qWait(20)
        workers = getattr(widget, "_workers", [])
        if all(w.isFinished() for w in workers):
            return


def test_quick_screenshot_runs_and_audits(qapp, tmp_path: Path) -> None:
    stub = _StubTool("截屏已保存：x.png")
    components = _components(tmp_path, stub, "screenshot")
    chat, ball, _ = _make_ball(qapp, with_radial=False)
    ball.show()

    quick_screenshot(components, ball)
    _wait_workers(ball)

    assert stub.calls == [{}]
    records = (tmp_path / "data" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"screenshot"' in records
    assert "quick_action" in records


def test_quick_organize_confirm_flow(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """整理桌面：方案确认 Yes + 确认词正确 → 真正执行。"""
    stub = _StubTool("方案或结果")
    components = _components(tmp_path, stub, "organize_desktop")
    chat, ball, _ = _make_ball(qapp, with_radial=False)
    ball.show()

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (CONFIRM_PHRASE, True))
    )

    quick_organize_desktop(components, ball)
    _wait_workers(ball)

    # dry_run 方案一次 + 正式执行一次
    assert stub.calls == [{"dry_run": True}, {"dry_run": False}]
    records = (tmp_path / "data" / "audit.jsonl").read_text(encoding="utf-8")
    assert records.count('"organize_desktop"') == 2
    assert '"allowed": true' in records


def test_quick_organize_wrong_phrase_aborts(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认词错误 → 不执行，审计记录拒绝。"""
    stub = _StubTool("方案")
    components = _components(tmp_path, stub, "organize_desktop")
    chat, ball, _ = _make_ball(qapp, with_radial=False)
    ball.show()

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("随便输", True)))

    quick_organize_desktop(components, ball)
    QTest.qWait(100)

    assert stub.calls == [{"dry_run": True}]  # 没有第二次执行
    records = (tmp_path / "data" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"allowed": false' in records
    assert "二次确认词不正确" in records
