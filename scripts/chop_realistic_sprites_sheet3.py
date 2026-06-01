#!/usr/bin/env python3
"""Chop missing realistic sprites from Gemini supplement sheets.

June 2026 — 3×3 grid (Gemini_Generated_Image_tijgtgtijgtgtijg.png):
  1  barn_owl
  2  brown_creeper
  3  forsters_tern
  4  great_horned_owl
  5  green_winged_teal
  6  western_grebe
  7  whimbrel
  8  white_breasted_nuthatch
  9  white_crowned_sparrow

May 2026 — 2×4 grid (Gemini_Generated_Image_a7njfza7njfza7nj.png) used cells
1, 4, 5, 6, 8 for lesser_goldfinch, black_headed_grosbeak, wilsons_warbler,
red_shouldered_hawk, caspian_tern (already in realistic-sprites/).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/mattdennis/Downloads/Gemini_Generated_Image_tijgtgtijgtgtijg.png")
OUT_DIR = ROOT / "realistic-sprites"

COLS = 3
ROWS = 3
INSET = 2
SPRITE_SIZE = 254

# slug → 1-based cell
REMAP: dict[str, int] = {
    "barn_owl": 1,
    "brown_creeper": 2,
    "forsters_tern": 3,
    "great_horned_owl": 4,
    "green_winged_teal": 5,
    "western_grebe": 6,
    "whimbrel": 7,
    "white_breasted_nuthatch": 8,
    "white_crowned_sparrow": 9,
}


def crop_cell(src: Image.Image, cell: int) -> Image.Image:
    w, h = src.size
    row, col = divmod(cell - 1, COLS)
    margin_x = (w - (w // COLS) * COLS) // 2
    cell_w, cell_h = w // COLS, h // ROWS
    x0 = margin_x + col * cell_w + INSET
    y0 = row * cell_h + INSET
    x1 = margin_x + (col + 1) * cell_w - INSET
    y1 = (row + 1) * cell_h - INSET
    return src.crop((x0, y0, x1, y1))


def fit_sprite(cell: Image.Image, size: int = SPRITE_SIZE) -> Image.Image:
    cell = cell.convert("RGBA")
    pad = 16
    cell.thumbnail((size - pad, size - pad), Image.Resampling.LANCZOS)
    dest = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    dest.paste(cell, ((size - cell.width) // 2, (size - cell.height) // 2), cell)
    return dest


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"source sheet not found: {SOURCE}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(SOURCE).convert("RGBA")

    for slug, cell in sorted(REMAP.items()):
        out = OUT_DIR / f"{slug}.png"
        fit_sprite(crop_cell(sheet, cell)).save(out)
        print(f"  {slug}.png <- sheet3 cell {cell}")

    print(f"wrote {len(REMAP)} sprites to {OUT_DIR}")


if __name__ == "__main__":
    main()
