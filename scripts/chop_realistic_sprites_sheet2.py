#!/usr/bin/env python3
"""Chop missing realistic sprites from the 12×6 Gemini supplement sheet."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/mattdennis/Downloads/Gemini_Generated_Image_t9fs08t9fs08t9fs.png")
OUT_DIR = ROOT / "realistic-sprites"

COLS = 12
ROWS = 6
INSET = 1

# slug → 1-based cell; see docs/realistic-sprites-gemini-legend-sheet2.md
REMAP: dict[str, int] = {
    "california_towhee": 13,       # R2 C1
    "wrentit": 15,                 # R2 C3
    "cliff_swallow": 29,           # R3 C5 (square tail; legend C4 is Barn Swallow)
    "allens_hummingbird": 42,      # R4 C6
    "ash_throated_flycatcher": 49,  # R5 C1
    "nuttalls_woodpecker": 53,     # R5 C5 (ladder-back; sheet 1 cell 19 is wrong)
}

# Still not on either sheet with usable art
STILL_MISSING = (
    "wilsons_warbler",
    "lesser_goldfinch",
    "black_headed_grosbeak",
    "caspian_tern",
)


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


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"source sheet not found: {SOURCE}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(SOURCE).convert("RGBA")

    for slug, cell in sorted(REMAP.items()):
        out = OUT_DIR / f"{slug}.png"
        crop_cell(sheet, cell).save(out)
        print(f"  {slug}.png <- sheet2 cell {cell}")

    print(f"wrote {len(REMAP)} sprites to {OUT_DIR}")
    print(f"still missing ({len(STILL_MISSING)}): {', '.join(STILL_MISSING)}")


if __name__ == "__main__":
    main()
