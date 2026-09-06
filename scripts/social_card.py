"""Render the 1200x630 social card used for link previews.

    python scripts/social_card.py

Writes docs/brand/png/lumen-social.png, which is what `og:image` points at and
what should be uploaded as the repository's social preview. It is generated
rather than drawn by hand so the palette can only ever be the brand's own
(docs/brand/palette.json) and the card can be re-rendered when the wordmark
changes.

The card shows the one thing the product does: a row of zones, each the colour
of an agent tab's status. Amber is working, red is needs you, green is done —
the same three colours the dashboard legend names.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "brand" / "png"
OUT = BRAND / "lumen-social.png"

W, H = 1200, 630
BG = (11, 12, 16)
TEXT = (238, 240, 245)
MUTED = (154, 160, 176)
PANEL = (28, 32, 41)
BORDER = (39, 44, 54)
AMBER, RED, GREEN = (251, 191, 36), (248, 113, 113), (74, 222, 128)
VIOLET_FIELD, CYAN_FIELD = (220, 200, 246), (184, 234, 243)

# Segoe first (this is rendered on Windows), then the usual Linux/macOS faces, so
# the script still produces a card on a machine that has none of the first ones.
FONTS = {
    "semibold": ["seguisb.ttf", "segoeuisb.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc"],
    "regular": ["segoeui.ttf", "DejaVuSans.ttf", "Helvetica.ttc"],
}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in FONTS[weight]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def field(size: tuple[int, int], color: tuple[int, int, int], strength: float) -> Image.Image:
    """One of the two static brand fields, as a soft radial wash."""
    mask = Image.radial_gradient("L").resize(size).point(lambda v: int((255 - v) * strength))
    wash = Image.new("RGB", size, color)
    return Image.composite(wash, Image.new("RGB", size, BG), mask)


def rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def build() -> Image.Image:
    card = Image.new("RGB", (W, H), BG)
    # The two brand fields, top corners, far too faint to read as status.
    card.paste(field((900, 620), CYAN_FIELD, 0.055), (620, -260))
    card.paste(field((820, 560), VIOLET_FIELD, 0.05), (-190, -230))

    wordmark = Image.open(BRAND / "lumen-logo-white.png").convert("RGBA")
    scale = 300 / wordmark.width
    wordmark = wordmark.resize((300, round(wordmark.height * scale)), Image.LANCZOS)
    card.paste(wordmark, (80, 92), wordmark)

    draw = ImageDraw.Draw(card)
    draw.text((80, 214), "Something happens on your computer,", font=font("semibold", 46), fill=TEXT)
    draw.text((80, 272), "the devices around you react.", font=font("semibold", 46), fill=TEXT)
    draw.text((80, 348), "RGB keyboards, light bars, smart lights, screen and notifications", font=font("regular", 26), fill=MUTED)
    draw.text((80, 386), "for Claude Code, Codex, builds and scripts. Local, no account.", font=font("regular", 26), fill=MUTED)

    # A keyboard's zones, one per agent tab, in the statuses the legend names.
    top, height, gap, left = 470, 78, 16, 80
    width = (W - left * 2 - gap * 3) // 4
    for i, color in enumerate((AMBER, RED, GREEN, None)):
        box = (left + i * (width + gap), top, left + i * (width + gap) + width, top + height)
        rounded(draw, box, 14, color or PANEL, outline=None if color else BORDER)
    labels = ("working", "needs you", "done", "no tab open")
    for i, text in enumerate(labels):
        draw.text((left + i * (width + gap), top + height + 16), text, font=font("regular", 22), fill=MUTED)
    return card


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
