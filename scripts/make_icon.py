"""生成 Fairy 极简图标（蓝白线条）。

输出：
- assets/fairy.ico 与 assets/fairy-icon.png（快捷方式 / exe 图标）
- src/fairy/ui/assets/radial-{cmd,shot,organize,mic}.png（放射菜单按钮图标，
  白色线条、透明底，随包分发）

设计：纯几何线条——外环/内弧/瞳点（眼睛）；指令气泡、相机、桌面格子、麦克风。
纯代码绘制（Pillow），不含任何外部素材，可重复生成：

    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# 主色：克莱因蓝系
BLUE = (37, 99, 235, 255)  # #2563EB
BLUE_LIGHT = (96, 165, 250, 255)  # #60A5FA
WHITE = (245, 248, 255, 255)  # 放射按钮上的线条色

SIZE = 1024  # 大尺寸绘制后降采样，抗锯齿
CENTER = SIZE // 2

OUTER_R = 420  # 外环半径
OUTER_W = 64  # 外环线宽
INNER_R = 240  # 内弧半径
INNER_W = 48  # 内线宽
PUPIL_R = 96  # 瞳点半径

ROOT = Path(__file__).resolve().parent.parent
OUT_ICO = ROOT / "assets" / "fairy.ico"
OUT_PNG = ROOT / "assets" / "fairy-icon.png"
OUT_RADIAL = ROOT / "src" / "fairy" / "ui" / "assets"


def draw_app_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 外环：完整圆环
    d.ellipse(
        [CENTER - OUTER_R, CENTER - OUTER_R, CENTER + OUTER_R, CENTER + OUTER_R],
        outline=BLUE,
        width=OUTER_W,
    )

    # 内弧：偏左上的开口圆环（眼睛的神态），缺口朝右下
    d.arc(
        [CENTER - INNER_R, CENTER - INNER_R, CENTER + INNER_R, CENTER + INNER_R],
        start=100,  # 缺口约 60°
        end=400,
        fill=BLUE_LIGHT,
        width=INNER_W,
    )

    # 瞳点：实心小圆，位于内弧缺口方向（右下），形成"注视"感
    px, py = CENTER + 150, CENTER + 150
    d.ellipse([px - PUPIL_R, py - PUPIL_R, px + PUPIL_R, py + PUPIL_R], fill=BLUE)

    return img


# ------------------------------------------------------------------
# 放射菜单按钮图标（512 绘制 → 128 输出，白色线条透明底）
# ------------------------------------------------------------------
_R = 512  # 放射图标绘制尺寸
_RI = 40  # 线宽


def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (_R, _R), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def draw_radial_cmd() -> Image.Image:
    """指令：圆角对话框气泡（含小尾巴）。"""
    img, d = _new()
    x0, y0, x1, y1 = 72, 96, 440, 368
    d.rounded_rectangle([x0, y0, x1, y1], radius=64, outline=WHITE, width=_RI)
    # 尾巴（左下小三角）
    d.polygon([(150, 368), (150, 440), (240, 368)], outline=WHITE, width=_RI)
    # 框内两个点，示意输入
    d.ellipse([200, 208, 248, 256], fill=WHITE)
    d.ellipse([280, 208, 328, 256], fill=WHITE)
    return img


def draw_radial_shot() -> Image.Image:
    """截屏：相机——圆角机身 + 顶部取景凸起 + 镜头圆。"""
    img, d = _new()
    # 取景器凸起
    d.rounded_rectangle([196, 84, 316, 140], radius=20, outline=WHITE, width=_RI)
    # 机身
    d.rounded_rectangle([64, 140, 448, 400], radius=56, outline=WHITE, width=_RI)
    # 镜头
    r = 84
    d.ellipse([256 - r, 270 - r, 256 + r, 270 + r], outline=WHITE, width=_RI)
    return img


def draw_radial_organize() -> Image.Image:
    """整理桌面：2x2 图标格子，右下角实心（被归位的那个）。"""
    img, d = _new()
    cells = [(96, 96), (288, 96), (96, 288), (288, 288)]
    w = 128
    for i, (x, y) in enumerate(cells):
        box = [x, y, x + w, y + w]
        if i == 3:
            d.rounded_rectangle(box, radius=28, fill=WHITE)
        else:
            d.rounded_rectangle(box, radius=28, outline=WHITE, width=_RI)
    return img


def draw_radial_mic() -> Image.Image:
    """说话：麦克风——胶囊咪头 + 托弧 + 立杆 + 底座。"""
    img, d = _new()
    # 咪头：竖直胶囊
    d.rounded_rectangle([196, 64, 316, 288], radius=60, outline=WHITE, width=_RI)
    # 托弧：下半圆环托住咪头
    d.arc([136, 128, 376, 368], start=0, end=180, fill=WHITE, width=_RI)
    # 立杆
    d.line([(256, 368), (256, 424)], fill=WHITE, width=_RI)
    # 底座横线
    d.line([(176, 424), (336, 424)], fill=WHITE, width=_RI)
    return img


def main() -> None:
    img = draw_app_icon().resize((256, 256), Image.LANCZOS)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT_PNG)
    img.save(OUT_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成：{OUT_PNG} 与 {OUT_ICO}")

    OUT_RADIAL.mkdir(parents=True, exist_ok=True)
    # 托盘 / 通知用的 app 图标也进包内资源
    img.save(OUT_RADIAL / "app-icon.png")
    for name, fn in (
        ("radial-cmd.png", draw_radial_cmd),
        ("radial-shot.png", draw_radial_shot),
        ("radial-organize.png", draw_radial_organize),
        ("radial-mic.png", draw_radial_mic),
    ):
        icon = fn().resize((128, 128), Image.LANCZOS)
        icon.save(OUT_RADIAL / name)
        print(f"已生成：{OUT_RADIAL / name}")


if __name__ == "__main__":
    main()
