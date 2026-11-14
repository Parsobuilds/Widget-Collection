"""Generate neutral multi-resolution Windows icons with Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "build"
    destination.mkdir(exist_ok=True)
    for name, color in (("usage", "#61d9b0"), ("monitor", "#81afff")):
        path = destination / f"{name}_icon.ico"
        if path.exists():
            continue
        image = Image.new("RGBA", (256, 256))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 248, 248), radius=52, fill="#172333")
        if name == "usage":
            for index, height in enumerate((65, 115, 165)):
                x = 48 + index * 58
                draw.rounded_rectangle((x, 212 - height, x + 38, 212), radius=8, fill=color)
        else:
            draw.rounded_rectangle((34, 43, 222, 178), radius=14, outline=color, width=14)
            draw.line((128, 178, 128, 213), fill=color, width=14)
            draw.line((82, 213, 174, 213), fill=color, width=14)
            draw.line((52, 119, 82, 119, 105, 80, 133, 145, 158, 105, 205, 105),
                      fill=color, width=10)
        image.save(path, sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])


if __name__ == "__main__":
    main()
