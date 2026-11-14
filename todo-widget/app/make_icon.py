"""
Builds todo_icon.ico for the tray icon and shortcut
==================================================

Run once at install time (`python make_icon.py`). Draws a dark rounded square with a cyan
border and an emerald checkmark at several sizes, then saves them as a single .ico file.

Educational note: an .ico is just a container of PNG images at different sizes, which is
why we draw the picture large and let Pillow shrink it for each entry.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
SCALE = 4  # draw big, then shrink with smooth resampling


def draw_icon(size: int) -> Image.Image:
    """Returns one icon image at the requested pixel size."""
    big = size * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    pad = int(big * 0.06)
    radius = int(big * 0.22)
    draw.rounded_rectangle(
        (pad, pad, big - pad, big - pad),
        radius=radius,
        fill="#101218",
        outline="#22D3EE",
        width=max(2, big // 32),
    )

    width = max(2, big // 14)
    draw.line(
        [(big * 0.24, big * 0.52), (big * 0.42, big * 0.68), (big * 0.76, big * 0.32)],
        fill="#34D399",
        width=width,
        joint="curve",
    )
    draw.line(
        [(big * 0.22, big * 0.84), (big * 0.78, big * 0.84)],
        fill="#AEB8CC",
        width=max(1, big // 42),
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "todo_icon.ico")
    frames = [draw_icon(size) for size in SIZES]
    frames[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES])
    frames[-1].save(os.path.join(here, "todo_icon.png"))
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
