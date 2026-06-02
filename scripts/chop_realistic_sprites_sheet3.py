#!/usr/bin/env python3
"""Chop missing realistic sprites from Gemini supplement sheets.

June 2026 — 4×5 grid (Gemini_Generated_Image_169vmp169vmp169v.png): common SB
supplement — cells 1–20 below (ruby-crowned kinglet … ring-billed gull).

June 2026 — 4×5 grid (Gemini_Generated_Image_qhxn18qhxn18qhxn.png): prod
detections — huttons_vireo … wild_turkey (already in realistic-sprites/).

June 2026 — 3×3 grid (Gemini_Generated_Image_tijgtgtijgtgtijg.png):
  1  barn_owl … 9  white_crowned_sparrow (already in realistic-sprites/).

May 2026 — 2×4 grid (Gemini_Generated_Image_a7njfza7njfza7nj.png) used cells
1, 4, 5, 6, 8 for lesser_goldfinch, black_headed_grosbeak, wilsons_warbler,
red_shouldered_hawk, caspian_tern (already in realistic-sprites/).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/mattdennis/Downloads/Gemini_Generated_Image_169vmp169vmp169v.png")
OUT_DIR = ROOT / "realistic-sprites"

COLS = 5
ROWS = 4
INSET = 2
SPRITE_SIZE = 254

# slug → 1-based cell
REMAP: dict[str, int] = {
    "ruby_crowned_kinglet": 1,
    "yellow_rumped_warbler": 2,
    "american_robin": 3,
    "northern_flicker": 4,
    "california_thrasher": 5,
    "hooded_oriole": 6,
    "golden_crowned_sparrow": 7,
    "townsends_warbler": 8,
    "says_phoebe": 9,
    "cassins_kingbird": 10,
    "belted_kingfisher": 11,
    "phainopepla": 12,
    "warbling_vireo": 13,
    "lark_sparrow": 14,
    "hermit_thrush": 15,
    "white_tailed_kite": 16,
    "tree_swallow": 17,
    "brandts_cormorant": 18,
    "heermanns_gull": 19,
    "ring_billed_gull": 20,
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
