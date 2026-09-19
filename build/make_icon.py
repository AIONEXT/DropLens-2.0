#!/usr/bin/env python3
"""Generate the DropLens application icon (build/appicon.ico).

Pure Pillow; produces a 256px rounded-square marc: magnifying-glass over a
folder with a green drop/"arrow-down" accent. Output sizes: 16,24,32,48,64,128,256.
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont


def rounded(draw: ImageDraw.ImageDraw, box, radius: float, fill):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0
    pad = 12 * s

    # rounded square background with soft vertical gradient
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dbg = ImageDraw.Draw(bg)
    for i in range(size):
        t = i / max(size - 1, 1)
        r = int(24 + (18 - 24) * t)
        g = int(58 + (96 - 58) * t)
        b = int(128 + (164 - 128) * t)
        dbg.line([(0, i), (size, i)], fill=(r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=22 * s, fill=255)
    img.paste(bg, (0, 0), mask)

    # magnifying glass (white ring + lens)
    ring_r = 62 * s
    cx, cy = size * 0.46, size * 0.44
    stroke = max(6, int(14 * s))
    if ring_r > stroke:
        d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=(255, 255, 255, 235), width=stroke)
    lens_r = ring_r - stroke
    if lens_r > 2:
        d.ellipse([cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r], fill=(185, 215, 250, 120))
    # handle
    hw = max(7, int(16 * s))
    hx0, hy0 = cx + ring_r * 0.62, cy + ring_r * 0.62
    hx1, hy1 = size * 0.92, size * 0.90
    d.line([(hx0, hy0), (hx1, hy1)], fill=(255, 255, 255, 235), width=hw)

    # green drop / arrow-down accent (arrives into the lens)
    ax = size * 0.78
    ay0, ay1 = size * 0.06, size * 0.32
    d.line([(ax, ay0), (ax, ay1)], fill=(106, 214, 135, 255), width=max(5, int(12 * s)), joint="curve")
    wing = max(9, int(26 * s))
    d.line([(ax - wing, ay1 - wing), (ax, ay1 + max(2, int(6 * s)))], fill=(106, 214, 135, 255), width=max(5, int(12 * s)))
    d.line([(ax + wing, ay1 - wing), (ax, ay1 + max(2, int(6 * s)))], fill=(106, 214, 135, 255), width=max(5, int(12 * s)))

    return img


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "build\\appicon.ico"
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make(s) for s in sizes]
    imgs[-1].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    print(f"icon written: {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())