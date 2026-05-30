#!/usr/bin/env python3
"""Re-chop realistic bird sprites from the Gemini sheet using slug→cell remap."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/mattdennis/Downloads/Gemini_Generated_Image_2nuqha2nuqha2nuq.png")
OUT_DIR = ROOT / "realistic-sprites"

# HIGH-confidence slug → 1-based cell (from docs/realistic-sprites-remap-plan.md)
REMAP: dict[str, int] = {
    "mourning_dove": 1,
    "house_finch": 2,
    "annas_hummingbird": 3,
    "turkey_vulture": 4,
    "california_scrub_jay": 6,
    "common_raven": 21,
    "western_gull": 58,
    "black_phoebe": 9,
    "european_starling": 10,
    "eurasian_collared_dove": 12,
    "song_sparrow": 13,
    "mallard": 14,
    "california_quail": 15,
    "red_tailed_hawk": 17,
    # nuttalls_woodpecker: sheet 2 cell 53 (sheet 1 cell 19 is wrong art)
    "american_crow": 20,
    "bushtit": 25,
    "brown_pelican": 27,
    "northern_mockingbird": 30,
    "spotted_towhee": 33,
    "brewers_blackbird": 34,
    "pacific_slope_flycatcher": 35,
    "double_crested_cormorant": 36,
    "red_winged_blackbird": 37,
    "bewicks_wren": 38,
    "oak_titmouse": 23,
    "brown_headed_cowbird": 40,
    "acorn_woodpecker": 41,
    "killdeer": 42,
    "house_sparrow": 44,
    "barn_swallow": 11,
    "orange_crowned_warbler": 48,
    "bullocks_oriole": 43,
    "american_coot": 50,
    "western_kingbird": 51,
    "western_bluebird": 52,
    "common_yellowthroat": 53,
    "great_egret": 55,
    "great_blue_heron": 56,
    "rock_pigeon": 57,
    "california_gull": 58,
}

# REGEN + MED — remove stale wrong chops
SKIP = {
    "california_towhee",
    "cliff_swallow",
    "wilsons_warbler",
    "wrentit",
    "lesser_goldfinch",
    "black_headed_grosbeak",
    "ash_throated_flycatcher",
    "allens_hummingbird",
    "caspian_tern",
}


def crop_cell(src: Image.Image, cell: int) -> Image.Image:
    row, col = divmod(cell - 1, 8)
    x0, y0 = col * 256 + 1, row * 256 + 1
    x1, y1 = (col + 1) * 256 - 1, (row + 1) * 256 - 1
    return src.crop((x0, y0, x1, y1))


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"source sheet not found: {SOURCE}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(SOURCE)

    removed: list[str] = []
    for slug in sorted(SKIP):
        path = OUT_DIR / f"{slug}.png"
        if path.exists():
            path.unlink()
            removed.append(slug)

    written: list[tuple[str, int]] = []
    for slug, cell in sorted(REMAP.items()):
        out = OUT_DIR / f"{slug}.png"
        crop_cell(sheet, cell).save(out)
        written.append((slug, cell))

    print(f"wrote {len(written)} sprites to {OUT_DIR}")
    for slug, cell in written:
        print(f"  {slug}.png <- cell {cell}")
    if removed:
        print(f"removed {len(removed)} skipped slugs:")
        for slug in removed:
            print(f"  {slug}.png")


if __name__ == "__main__":
    main()
