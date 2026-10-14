"""Build the published artwork in docs/assets/ from the masters in docs/assets/source/.

    uv run --no-project --with pillow==12.3.0 scripts/build_assets.py

Masters are the Gemini images (WebP, full resolution). Logos and icons are cut out to their
rounded square with transparent corners (the Gemini page backgrounds differ), then scaled.
logo.svg / logo-light.svg are hand-written and not generated here.
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/assets/source"
OUT = ROOT / "docs/assets"
SS = 4  # supersampling for smooth mask edges

NAVY, LILAC, DARK_LILAC, AMBER = (35, 34, 76), (146, 125, 226), (106, 88, 184), (247, 160, 40)
ICONS = ["sync", "reminders", "session-notes", "conflicts", "signing", "deploy-key", "distill",
         "doctor", "team"]


def master(name: str) -> Image.Image:
    return Image.open(SRC / f"{name}.webp").convert("RGB")


def cut_tile(im: Image.Image) -> Image.Image:
    """Crop to the rounded square and make everything outside it transparent."""
    bg = im.getpixel((5, 5))
    diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L")
    mask = diff.point(lambda v: 255 if v > 40 else 0)
    left, top, right, bottom = mask.getbbox()
    # corner radius from the diagonal: first tile pixel at (l+k, t+k) → k = R(1 - 1/√2)
    k = next(k for k in range(min(right - left, bottom - top)) if mask.getpixel((left + k, top + k)))
    radius = k / (1 - 1 / math.sqrt(2))
    tile = im.crop((left, top, right, bottom)).convert("RGBA")
    w, h = tile.size
    big = Image.new("L", (w * SS, h * SS), 0)
    inset = 1.5 * SS  # stay inside the antialiased fringe
    ImageDraw.Draw(big).rounded_rectangle(
        (inset, inset, w * SS - inset, h * SS - inset), radius=radius * SS, fill=255)
    tile.putalpha(big.resize((w, h), Image.LANCZOS))
    s = max(w, h)  # pad to an exact square (Gemini tiles can be a few px off)
    square = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    square.paste(tile, ((s - w) // 2, (s - h) // 2))
    return square


def scaled(im: Image.Image, width: int, height: int | None = None) -> Image.Image:
    return im.resize((width, height or round(width * im.height / im.width)), Image.LANCZOS)


def save(im: Image.Image, name: str) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".webp":
        im.save(path, "WEBP", quality=88, method=6, exact=False)
    elif path.suffix == ".jpg":
        im.convert("RGB").save(path, "JPEG", quality=85, optimize=True, progressive=True)
    else:
        im.save(path, optimize=True)
    print(f"{path.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}  {path.stat().st_size // 1024} KB")


def simple_glyph(size: int) -> Image.Image:
    """The logo reduced to square, ring and core — legible at favicon sizes."""
    s = size * 16
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.2, fill=NAVY)
    c = s / 2

    def disc(r: float, fill: tuple) -> None:
        d.ellipse((c - r * s, c - r * s, c + r * s, c + r * s), fill=fill)

    disc(0.40, DARK_LILAC)
    d.pieslice((c - 0.40 * s, c - 0.40 * s, c + 0.40 * s, c + 0.40 * s), 200, 380, fill=LILAC)
    disc(0.19, NAVY)
    disc(0.12, AMBER)
    return im.resize((size, size), Image.LANCZOS)


def main() -> None:
    pair = master("logo-pair")
    half = pair.width // 2
    light = cut_tile(pair.crop((0, 0, half, pair.height)))
    dark = cut_tile(pair.crop((half, 0, pair.width, pair.height)))
    save(scaled(light, 512), "logo-light.webp")
    save(scaled(dark, 512), "logo-dark.webp")

    for name in ICONS:
        save(scaled(cut_tile(master(f"icon-{name}")), 256), f"icons/{name}.webp")

    for name in ("banner-light", "banner-dark", "collective-knowledge"):
        save(scaled(master(name), 1280), f"{name}.webp")

    # GitHub's social preview: 1280x640 (2:1), PNG/JPG/GIF only
    im = master("social-preview")
    w = im.height * 2
    x = (im.width - w) // 2
    save(scaled(im.crop((x, 0, x + w, im.height)), 1280, 640), "social-preview.jpg")

    sizes = {16: simple_glyph(16), 32: simple_glyph(32),
             48: scaled(dark, 48), 64: scaled(dark, 64)}
    ico = OUT / "favicon.ico"
    sizes[64].save(ico, sizes=[(n, n) for n in sizes], append_images=[sizes[n] for n in (16, 32, 48)])
    print(f"{ico.relative_to(ROOT)}  {sorted(Image.open(ico).info['sizes'])}  {ico.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
