"""生成 Fairy 极简图标（蓝白线条），输出 assets/fairy.ico 与 assets/fairy-icon.png。

设计：深蓝线条在透明背景上构成「眼睛」意象——外环、内弧、瞳点。
纯代码绘制（Pillow），不含任何外部素材，可重复生成：

    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# 主色：克莱因蓝系
BLUE = (37, 99, 235, 255)  # #2563EB
BLUE_LIGHT = (96, 165, 250, 255)  # #60A5FA

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


def draw() -> Image.Image:
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


def main() -> None:
    img = draw().resize((256, 256), Image.LANCZOS)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT_PNG)
    img.save(OUT_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成：{OUT_PNG} 与 {OUT_ICO}")


if __name__ == "__main__":
    main()
