"""screenshot.py 单元测试：ImageGrab 打桩，验证保存路径与防覆盖。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from fairy.tools.base import ToolError
from fairy.tools.screenshot import ScreenshotTool


def _install_fake_imagegrab(size: tuple[int, int] = (100, 60)) -> None:
    """用纯色图桩掉 PIL.ImageGrab，避免真实截屏。"""
    module = types.ModuleType("PIL.ImageGrab")

    def fake_grab(all_screens: bool = False) -> Image.Image:
        return Image.new("RGB", size, color=(30, 60, 120))

    module.grab = fake_grab  # type: ignore[attr-defined]
    sys.modules["PIL.ImageGrab"] = module


@pytest.fixture(autouse=True)
def fake_grab():
    _install_fake_imagegrab()
    yield
    sys.modules.pop("PIL.ImageGrab", None)


def test_screenshot_saves_png(tmp_path: Path) -> None:
    result = ScreenshotTool(tmp_path).execute()
    assert "截屏已保存" in result
    assert "100x60" in result
    saved = list((tmp_path / "screenshots").glob("*.png"))
    assert len(saved) == 1
    assert Image.open(saved[0]).size == (100, 60)


def test_screenshot_never_overwrites(tmp_path: Path) -> None:
    tool = ScreenshotTool(tmp_path)
    # 伪造已存在的同名文件，验证追加序号
    out_dir = tmp_path / "screenshots"
    out_dir.mkdir()
    from datetime import datetime

    existing = out_dir / (datetime.now().strftime("screenshot-%Y%m%d-%H%M%S") + ".png")
    existing.write_bytes(b"old")
    result = tool.execute()
    assert "-1.png" in result or existing.read_bytes() == b"old"


def test_screenshot_outside_workspace_impossible(tmp_path: Path) -> None:
    """保存目录固定在工作区 screenshots/ 内。"""
    ScreenshotTool(tmp_path).execute(all_screens=True)
    saved = list((tmp_path / "screenshots").glob("*.png"))
    assert len(saved) == 1
    assert saved[0].resolve().is_relative_to(tmp_path.resolve())


def test_screenshot_without_pillow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pillow 缺失时给出可读错误。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if name.startswith("PIL"):
            raise ImportError("No module named 'PIL'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ToolError, match="Pillow"):
        ScreenshotTool(tmp_path).execute()
