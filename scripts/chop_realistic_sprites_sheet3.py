#!/usr/bin/env python3
"""Chop missing realistic sprites from the 2×4 Gemini supplement sheet (May 2026).

Legend (row-major, 1-based cells) — see user-provided Gemini description:
  1  lesser_goldfinch (male)
  2  lesser goldfinch female — skip
  3  yellow warbler — wrong species, skip
  4  black_headed_grosbeak
  5  wilsons_warbler
  6  red_shouldered_hawk
  7  brown finch variant — skip
  8  caspian_tern
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/mattdennis/Downloads/Gemini_Generated_Image_a7njfza7njfza7nj.png")
OUT_DIR = ROOT / "realistic-sprites"

COLS = 4
ROWS = 2
INSET = 2
SPRITE_SIZE = 254

# slug → 1-based cell
REMAP: dict[str, int] = {
    "lesser_goldfinch": 1,
    "black_headed_grosbeak": 4,
    "wilsons_warbler": 5,
    "red_shouldered_hawk": 6,
    "caspian_tern": 8,
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
