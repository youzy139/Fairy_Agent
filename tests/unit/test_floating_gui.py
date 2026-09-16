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
from PySide6.QtGui import QGuiApplication, QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fairy.safety.audit import AuditLogger  # noqa: E402
from fairy.tools.registry import ToolRegistry  # noqa: E402
from fairy.ui import floating  # noqa: E402
from fairy.ui.floating import (  # noqa: E402
    BALL_SIZE,
    ORGANIZE_INSTRUCTION,
    CommandBar,
    FloatingBall,
    GuiComponents,
    RadialAction,
    RadialMenu,
    Toast,
    _ToolCallEmitter,
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


def _components(tmp_path: Path, agent=None) -> GuiComponents:
    return GuiComponents(
        agent=agent or _stub_agent(),
        audit=AuditLogger(tmp_path / "data"),
        bridge=None,
        registry=ToolRegistry(),
        workspace=str(tmp_path),
        emitter=_ToolCallEmitter(),
    )


def _make_ball(qapp):
    """造一个悬浮球 + 放射菜单，返回 (ball, menu, calls)。"""
    calls: list[str] = []
    ball = FloatingBall(on_quit=lambda: None, on_double_click=lambda: calls.append("double"))
    actions = [
        RadialAction("cmd", "指令", lambda: calls.append("chat")),
        RadialAction("shot", "截屏", lambda: calls.append("shot")),
        RadialAction("organize", "整理桌面", lambda: calls.append("organize")),
    ]
    menu = RadialMenu(actions)
    ball.set_radial(menu)
    return ball, menu, calls


def test_eye_image_exists() -> None:
    assert os.path.isfile(eye_image_path())


def test_floating_ball_creation(qapp) -> None:
    ball, menu, _ = _make_ball(qapp)
    assert ball.width() == BALL_SIZE
    assert ball.height() == BALL_SIZE
    assert not ball._pixmap.isNull()
    assert not menu.isVisible()


def test_ball_click_toggles_radial_menu(qapp) -> None:
    ball, menu, _ = _make_ball(qapp)
    ball.show()
    assert not menu.isVisible()

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert menu.isVisible()

    QTest.mouseClick(ball, Qt.MouseButton.LeftButton)
    assert not menu.isVisible()


def test_ball_double_click_triggers_callback(qapp) -> None:
    ball, menu, calls = _make_ball(qapp)
    ball.show()
    QTest.mouseDClick(ball, Qt.MouseButton.LeftButton)
    assert "double" in calls
    assert not menu.isVisible()


def test_radial_menu_buttons_and_actions(qapp) -> None:
    ball, menu, calls = _make_ball(qapp)
    assert len(menu.buttons) == 3
    # 按钮使用线条图标（非 emoji 文字）
    for button in menu.buttons:
        assert not button.icon().isNull()
    menu.buttons[0].click()
    assert "chat" in calls
    menu.buttons[1].click()
    assert "shot" in calls


def test_radial_menu_positions_around_ball(qapp) -> None:
    ball, menu, _ = _make_ball(qapp)
    ball.show()
    menu.toggle(ball)
    # 菜单中心与球心重合（离屏平台的窗口管理器有少量取整误差，容差 2px）
    delta = menu.geometry().center() - ball.geometry().center()
    assert abs(delta.x()) <= 2 and abs(delta.y()) <= 2


# --- 指令条 ---


def test_command_bar_send_and_reply(qapp, tmp_path: Path) -> None:
    bar = CommandBar(_components(tmp_path, _stub_agent("回复")))
    bar.show()  # 父窗口不显示时子控件 isVisible() 恒为 False
    bar._input.setText("你好")
    bar._send()

    for _ in range(50):
        QTest.qWait(20)
        if bar._worker is None:
            break

    assert "回复:你好" in bar._reply.toPlainText()
    assert bar._reply.isVisible()


def test_command_bar_empty_input_ignored(qapp, tmp_path: Path) -> None:
    bar = CommandBar(_components(tmp_path))
    bar._input.setText("   ")
    bar._send()
    assert bar._worker is None
    assert not bar._reply.isVisible()


def test_command_bar_esc_hides(qapp, tmp_path: Path) -> None:
    bar = CommandBar(_components(tmp_path))
    bar.show()
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    bar.keyPressEvent(event)
    assert not bar.isVisible()


def test_command_bar_tool_call_indicator(qapp, tmp_path: Path) -> None:
    bar = CommandBar(_components(tmp_path))
    bar._worker = SimpleNamespace()  # 伪装有任务在跑
    bar._components.emitter.called.emit("list_dir", '{"path": "."}')
    QTest.qWait(50)
    assert "list_dir" in bar._reply.toPlainText()
    assert "⚙" in bar._reply.toPlainText()


def test_command_bar_run_command(qapp, tmp_path: Path) -> None:
    bar = CommandBar(_components(tmp_path, _stub_agent("执行了")))
    bar.run_command("整理桌面")
    for _ in range(50):
        QTest.qWait(20)
        if bar._worker is None:
            break
    assert "执行了:整理桌面" in bar._reply.toPlainText()
    assert bar.isVisible()


def test_command_bar_reply_max_height_scrollable(qapp, tmp_path: Path) -> None:
    """超长回复限高 + 可滚动，不再溢出屏幕。"""
    bar = CommandBar(_components(tmp_path))
    long_text = "很长的回复\n" * 200
    bar._set_reply(long_text)
    assert bar._reply.height() <= CommandBar.MAX_REPLY_HEIGHT
    # 内容超过可视高度，滚动条可用
    assert bar._reply.verticalScrollBar().maximum() > 0


def test_command_bar_positions_below_ball(qapp, tmp_path: Path) -> None:
    """指令条出现在悬浮球下方。"""
    ball, _, _ = _make_ball(qapp)
    ball.show()
    bar = CommandBar(_components(tmp_path))
    bar.show_near(ball)
    assert bar.y() >= ball.y() + ball.height()
    # 水平方向与球居中对齐（含屏幕边缘钳制）
    assert abs(bar.geometry().center().x() - ball.geometry().center().x()) <= bar.width() // 2


def test_command_bar_follows_ball_drag(qapp, tmp_path: Path) -> None:
    """拖动悬浮球时指令条跟随。"""
    ball, _, _ = _make_ball(qapp)
    bar = CommandBar(_components(tmp_path))
    bar.show_near(ball)
    ball.show()
    # 模拟拖动：直接移动球并触发回调
    ball.move(200, 200)
    bar.follow(ball)
    assert abs(bar.y() - (ball.y() + ball.height() + 12)) <= 2


# --- 快捷动作 ---


def test_quick_screenshot_runs_and_audits(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """截屏快捷动作：桩掉 ScreenshotTool，验证执行、审计与剪贴板复制。"""
    from PIL import Image

    calls: list[dict] = []
    shot_path = tmp_path / "shot.png"

    class _StubScreenshot:
        def __init__(self, workspace, output_dir=None) -> None:
            assert output_dir is not None  # 快捷动作必须注入图片库目录

        def execute(self, **args) -> str:
            calls.append(args)
            Image.new("RGB", (10, 10), color=(30, 60, 120)).save(shot_path)
            return f"截屏已保存：{shot_path}（10x10）"

    monkeypatch.setattr(floating, "ScreenshotTool", _StubScreenshot)
    monkeypatch.setattr(floating, "QUICK_SCREENSHOT_DIR", tmp_path / "shots")

    components = _components(tmp_path)
    ball, _, _ = _make_ball(qapp)
    ball.show()

    quick_screenshot(components, ball)
    # 确定性等待：done 信号槽（含审计写入）跑完
    audit_file = tmp_path / "data" / "audit.jsonl"
    for _ in range(200):
        QTest.qWait(20)
        if audit_file.exists():
            break

    assert calls == [{}]
    records = audit_file.read_text(encoding="utf-8")
    assert '"screenshot"' in records
    assert "quick_action" in records
    # 截屏后应已复制到剪贴板
    assert not QGuiApplication.clipboard().image().isNull()


def test_quick_organize_sends_instruction(qapp, tmp_path: Path) -> None:
    """整理桌面快捷动作：不再直接动文件，而是给 Agent 下达语义摆位指令。"""
    sent: list[str] = []
    bar = SimpleNamespace(run_command=lambda text: sent.append(text))
    quick_organize_desktop(_components(tmp_path), bar)
    assert sent == [ORGANIZE_INSTRUCTION]
    assert "list_desktop_icons" in sent[0]
    assert "arrange_desktop" in sent[0]


def test_toast_click_callback(qapp) -> None:
    clicked: list[bool] = []
    ball, _, _ = _make_ball(qapp)
    ball.show()
    toast = Toast("测试提示", ball, on_click=lambda: clicked.append(True))
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    assert clicked == [True]
    assert not toast.isVisible()


# --- 眼睛状态动画 ---


def test_ball_state_machine(qapp) -> None:
    ball, _, _ = _make_ball(qapp)
    assert ball._state == "idle"

    ball.set_state("thinking")
    assert ball._state == "thinking"
    ball._tick()
    ball._tick()
    assert ball._angle == 3.0  # 每帧 1.5°

    ball.set_state("working")
    ball._tick()
    assert ball._angle == 8.0  # thinking 的 3.0 + working 每帧 5°

    ball.set_state("bad-state")  # 未知状态被忽略
    assert ball._state == "working"


def test_ball_ok_error_auto_return_idle(qapp) -> None:
    ball, _, _ = _make_ball(qapp)
    ball.set_state("ok")
    assert ball._hold == FloatingBall._HOLD_FRAMES
    for _ in range(FloatingBall._HOLD_FRAMES + 1):
        ball._tick()
    assert ball._state == "idle"
    assert ball._angle == 0.0

    ball.set_state("error")
    for _ in range(FloatingBall._HOLD_FRAMES + 1):
        ball._tick()
    assert ball._state == "idle"


def test_ball_idle_breathing(qapp) -> None:
    ball, _, _ = _make_ball(qapp)
    ball._tick()
    assert ball._scale != 1.0  # 呼吸缩放生效
    assert ball._state == "idle"


def test_command_bar_drives_ball_state(qapp, tmp_path: Path) -> None:
    """指令条一轮对话驱动状态：thinking →（工具）→ ok。"""
    states: list[str] = []
    components = _components(tmp_path, _stub_agent("好了"))
    components.emitter.stateChanged.connect(states.append)
    bar = CommandBar(components)
    bar._input.setText("你好")
    bar._send()
    for _ in range(50):
        QTest.qWait(20)
        if bar._worker is None:
            break
    assert states[0] == "thinking"
    assert states[-1] == "ok"


# --- 全局热键与托盘 ---


def test_parse_hotkey_basic() -> None:
    from fairy.ui.floating import parse_hotkey

    assert parse_hotkey("ctrl+shift+space") == (0xA2, 0xA0, 0x20)
    assert parse_hotkey("Ctrl+Alt+K") == (0xA2, 0xA4, ord("K"))
    assert parse_hotkey("f5") == (0x74,)
    with pytest.raises(ValueError, match="无法识别"):
        parse_hotkey("ctrl+banana")
    with pytest.raises(ValueError):
        parse_hotkey("")


def test_hotkey_triggers_once_per_press(qapp) -> None:
    """组合键按住期间只触发一次，松开后可再次触发。"""
    from fairy.ui.floating import HotkeyManager

    pressed_keys: set[int] = set()
    manager = HotkeyManager("ctrl+space", poll_ms=10, pressed_fn=lambda vk: vk in pressed_keys)
    fired: list[bool] = []
    manager.triggered.connect(lambda: fired.append(True))
    manager.start()

    pressed_keys.update({0xA2, 0x20})  # 按下 ctrl+space
    QTest.qWait(100)
    assert len(fired) == 1

    # 继续按住不重复触发
    QTest.qWait(100)
    assert len(fired) == 1

    pressed_keys.clear()  # 松开
    QTest.qWait(60)
    pressed_keys.update({0xA2, 0x20})  # 再次按下
    QTest.qWait(100)
    assert len(fired) == 2
    manager.stop()


def test_ball_toggle_visibility(qapp) -> None:
    ball, menu, _ = _make_ball(qapp)
    ball.show()
    menu.toggle(ball)
    ball.toggle_visibility()  # 隐藏球应连带收起菜单
    assert not ball.isVisible()
    assert not menu.isVisible()
    ball.toggle_visibility()
    assert ball.isVisible()


def test_setup_tray_offscreen(qapp, tmp_path: Path) -> None:
    """离屏/无托盘环境返回 None 而不是崩溃。"""
    from fairy.ui.floating import setup_tray

    ball, _, _ = _make_ball(qapp)
    bar = CommandBar(_components(tmp_path))
    tray = setup_tray(qapp, ball, bar)
    if tray is None:
        return  # 无托盘环境，符合预期
    tray.hide()  # 有托盘则用完收起来
