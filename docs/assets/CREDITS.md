# Artwork credits

The corkern artwork was generated with **Google Gemini** (image generation, October 2026) from
prompts written for this project, then selected and post-processed. Gemini embeds an invisible
SynthID watermark in the images it generates.

## Files

| File | Origin |
|---|---|
| `logo-light.webp`, `logo-dark.webp` | Gemini (one image with both variants), split and cut out, 512 px |
| `logo.svg`, `logo-light.svg` | Hand-written simplified vector version of the logo (written with Claude Code) |
| `favicon.ico` | 48 and 64 px from `logo-dark`; 16 and 32 px a reduced drawing (square, ring, core) that stays legible at that size |
| `icons/*.webp` | Gemini, cut out to their rounded squares, 256 px |
| `banner-light.webp`, `banner-dark.webp` | Gemini, scaled to 1280 px wide |
| `collective-knowledge.webp` | Gemini, scaled to 1280 px wide |
| `social-preview.jpg` | Gemini, centre-cropped to 2:1 and scaled to 1280x640 (GitHub's social preview size; GitHub accepts PNG, JPG or GIF there) |
| `source/*.webp` | The Gemini masters at full resolution (re-encoded from Gemini's JPEGs; `glyph.webp` is the reference for the SVG) |

## Rebuilding

Everything except the SVGs is generated from `source/`:

    uv run --no-project --with pillow==12.3.0 scripts/build_assets.py

Logos and icons are cut out to their rounded square with transparent corners, so they sit on any
background (the Gemini page backgrounds differ from image to image).
