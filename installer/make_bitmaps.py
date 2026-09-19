"""Generate branded Inno Setup wizard bitmaps (run from build_installer.ps1)."""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    from dropLens.ui.icon import app_icon

    OUT = "build"

    # Welcome page art (744x386): dark gradient + big icon + product text
    W, H = 744, 386
    bg = Image.new("RGBA", (W, H), (13, 17, 23, 255))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        c = (int(13 + 10 * t), int(17 + 8 * t), int(23 + 16 * t), 255)
        d.line([(0, y), (W, y)], fill=c)
    bg.alpha_composite(app_icon(210), (40, 85))
    d.text((40, 320), "Drop anything.  Scan everything.  Find it instantly.",
           fill=(139, 152, 169, 255))
    bg.save(os.path.join(OUT, "installer_welcome.bmp"))

    # Side image (164x314)
    W2, H2 = 164, 314
    side = Image.new("RGBA", (W2, H2), (13, 17, 23, 255))
    ds = ImageDraw.Draw(side)
    for y in range(H2):
        t = y / H2
        c = (int(13 + 14 * t), int(17 + 10 * t), int(23 + 22 * t), 255)
        ds.line([(0, y), (W2, y)], fill=c)
    side.alpha_composite(app_icon(150), (8, 82))
    side.save(os.path.join(OUT, "installer_side.bmp"))

    print("installer bitmaps written to", OUT)