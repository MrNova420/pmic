#!/usr/bin/env python3
"""Generate the PMic icon set (assets/pmic.ico, assets/pmic.png).

The mark matches the running tray icon: a dark disc inside an accent ring,
carrying a microphone silhouette. Rendering is done once at high resolution
and downsampled per size, so 16 px stays legible instead of turning to mush.

Run: python tools/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

INK = (237, 241, 247, 255)      # #EDF1F7
DIM = (138, 148, 163, 255)      # #8A94A3
ACCENT = (76, 194, 255, 255)    # #4CC2FF
CARD = (23, 28, 36, 255)        # #171C24
BG = (14, 17, 22, 255)          # #0E1116

S = 1024  # master render size


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = size / S

    def box(cx: float, cy: float, r: float) -> list[float]:
        return [(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k]

    # disc: flat card colour with a subtle darker outer ring for depth
    d.ellipse(box(S / 2, S / 2, 470), fill=BG)
    d.ellipse(box(S / 2, S / 2, 430), fill=CARD)

    # accent ring
    d.ellipse(box(S / 2, S / 2, 430), outline=ACCENT, width=int(46 * k))

    # microphone capsule
    cap_w, cap_top, cap_bot = 176, 322, 604
    d.rounded_rectangle(
        [(S / 2 - cap_w / 2) * k, cap_top * k, (S / 2 + cap_w / 2) * k, cap_bot * k],
        radius=(cap_w / 2) * k,
        fill=INK,
    )

    # capsule grille: two dim lines so large sizes read as a mic, not a pill
    if size >= 96:
        for y in (420, 490):
            d.line(
                [(S / 2 - cap_w / 2 + 34) * k, y * k, (S / 2 + cap_w / 2 - 34) * k, y * k],
                fill=DIM,
                width=int(12 * k),
            )

    # cradle arc around the capsule
    d.arc(
        [(S / 2 - 236 / 2) * k, 380 * k, (S / 2 + 236 / 2) * k, 700 * k],
        start=20, end=160, fill=INK, width=int(40 * k),
    )
    # stem + base
    d.line([(S / 2) * k, 690 * k, (S / 2) * k, 766 * k], fill=INK, width=int(40 * k))
    d.line([(S / 2 - 96) * k, 772 * k, (S / 2 + 96) * k, 772 * k],
           fill=INK, width=int(40 * k))

    return img if size == S else img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    frames = [render(s) for s in sizes]
    frames[-1].save(OUT / "pmic.ico", format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    render(512).save(OUT / "pmic.png")
    print(f"wrote {OUT / 'pmic.ico'} ({len(sizes)} sizes)")
    print(f"wrote {OUT / 'pmic.png'}")


if __name__ == "__main__":
    main()
