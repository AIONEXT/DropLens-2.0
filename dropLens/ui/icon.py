"""Runtime-generated spatial 'lens' app icon.

The icon is drawn with Pillow so the frozen .exe never needs to ship an
asset file — the same graphic is reused for the splash screen, the tray,
the drop zone and window decorations.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter


def _make(size: int) -> "Image.Image":
    size = max(size, 16)
    s = float(size)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded-square tile
    rad = int(s * 0.22)
    d.rounded_rectangle([s * 0.03, s * 0.03, s * 0.97, s * 0.97], radius=rad,
                        fill=(17, 22, 31, 255))
    # subtle vertical gradient overlay for depth
    grad = Image.new("L", (1, size), 0)
    for y in range(size):
        grad.putpixel((0, y), int(38 - 22 * (y / size)))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    overlay.putpixel((0, 0), (0, 0, 0, 0))
    for y in range(size):
        v = grad.getpixel((0, y))
        for x in range(size):
            overlay.putpixel((x, y), (120, 180, 255, v))
    img = Image.alpha_composite(img, overlay)

    # magnifying lens
    cx, cy = s * 0.46, s * 0.45
    r = s * 0.26
    lw = max(2, int(s * 0.07))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(61, 130, 246, 255), width=lw)
    d.ellipse([cx - r + lw * 1.4, cy - r + lw * 1.4,
               cx + r - lw * 1.4, cy + r - lw * 1.4], fill=(34, 211, 238, 255))
    # handle
    hx1, hy1 = cx + r * 0.72, cy + r * 0.72
    hx2, hy2 = cx + r * 1.35, cy + r * 1.35
    d.line([hx1, hy1, hx2, hy2], fill=(61, 130, 246, 255), width=lw)
    # little spark dot top
    d.ellipse([cx - r * 0.45, cy - r * 1.42, cx - r * 0.15, cy - r * 1.12],
              fill=(147, 197, 253, 255))
    d.text((cx - r * 0.05, cy - r * 0.5), "")

    return img.resize((size, size), Image.LANCZOS)


_CACHE: dict[int, "Image.Image"] = {}


def app_icon(size: int = 128) -> "Image.Image":
    """Return a cached app icon (PIL Image)."""
    if size not in _CACHE:
        _CACHE[size] = _make(size)
    return _CACHE[size]


def save_icon_file(path: str, size: int = 512) -> str:
    """Write a PNG copy of the app icon (used by the installer/build)."""
    app_icon(size).save(path, "PNG")
    return path