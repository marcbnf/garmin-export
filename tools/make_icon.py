#!/usr/bin/env python3
"""Generate the app icon (pulse line over an export tray, navy-to-teal).

    pip install pillow
    python tools/make_icon.py

Writes garmin_export/assets/icon.png (window icon), icon.icns (macOS) and
icon.ico (Windows). The generated files are committed, so Pillow is only
needed to change the icon.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "garmin_export" / "assets"
SIZE = 1024
SS = 4  # supersampling factor for smooth edges

TOP = (36, 59, 92)       # navy
BOTTOM = (14, 124, 134)  # teal
WHITE = (255, 255, 255, 255)
STROKE = 54

# Glyph geometry on a 1024 grid
PULSE = [(250, 420), (395, 420), (445, 320), (515, 540), (570, 380), (605, 420), (774, 420)]
TRAY = [(330, 640), (330, 735), (694, 735), (694, 640)]


def render() -> Image.Image:
    w = SIZE * SS
    s = lambda pts: [(x * SS, y * SS) for x, y in pts]  # noqa: E731
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))

    # macOS-style body: 824 px rounded square centered on the 1024 canvas
    inset, radius = 100 * SS, 185 * SS
    box = (inset, inset, w - inset, w - inset)

    shadow = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (box[0], box[1] + 14 * SS, box[2], box[3] + 14 * SS), radius, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * SS)))

    gradient = Image.new("RGBA", (w, w))
    gd = ImageDraw.Draw(gradient)
    for y in range(0, w, SS):
        t = y / w
        color = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        gd.rectangle((0, y, w, y + SS), fill=color + (255,))
    mask = Image.new("L", (w, w), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    img.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    width = STROKE * SS
    for line in (s(PULSE), s(TRAY)):
        draw.line(line, fill=WHITE, width=width, joint="curve")
        for x, y in (line[0], line[-1]):  # round caps
            draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=WHITE)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = render()
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    icon.save(OUT / "icon.icns")  # Pillow writes all standard ICNS sizes
    icon.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    for f in ("icon.png", "icon.icns", "icon.ico"):
        print(f"wrote {OUT / f} ({(OUT / f).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
