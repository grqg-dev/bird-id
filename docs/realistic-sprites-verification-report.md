# Realistic sprites verification report

Audit of 50 chopped PNGs in `realistic-sprites/` against `santa-barbara-top-birds.md`, cross-checked with the source sheet (`Gemini_Generated_Image_2nuqha2nuqha2nuq.png`) and extras cells 51–58.

**Geometry:** chop bounds look correct (256×256 cells, 1 px inset). **Problem:** Gemini painted the wrong species in many cells; filenames follow rank order, not painted content.

---

## Summary

| Status | Count |
|--------|------:|
| OK | 12 |
| CLOSE | 4 |
| UNCERTAIN | 1 |
| WRONG | 33 |
| **Total** | **50** |

### WRONG slugs (need fix)

`california_towhee`, `wilsons_warbler`, `american_crow`, `wrentit`, `bushtit`, `brown_pelican`, `lesser_goldfinch`, `northern_mockingbird`, `spotted_towhee`, `brewers_blackbird`, `pacific_slope_flycatcher`, `double_crested_cormorant`, `red_winged_blackbird`, `bewicks_wren`, `oak_titmouse`, `brown_headed_cowbird`, `acorn_woodpecker`, `killdeer`, `black_headed_grosbeak`, `house_sparrow`, `barn_swallow`, `allens_hummingbird`, `ash_throated_flycatcher`, `orange_crowned_warbler`, `bullocks_oriole`, `american_coot`, `western_kingbird`, `western_bluebird`, `common_yellowthroat`, `caspian_tern`, `great_egret`, `great_blue_heron`, `rock_pigeon`, `california_gull`

**Usable as-is (OK):** ranks 1–4, 6, 9–10, 12–15, 17 (12 slugs).

**Acceptable with caveat (CLOSE):** `common_raven`, `western_gull`, `cliff_swallow`, `nuttalls_woodpecker`.

### Missing top-50 species (no usable art under correct slug)

These do **not** appear correctly anywhere in the chopped set:

| Species | Notes |
|---------|-------|
| **California Towhee** | Cell 5 shows a hooded towhee (Spotted/Eastern type), not plain brown CA Towhee |
| **Wrentit** | Not found on sheet |
| **Cliff Swallow** | Only Barn Swallow painted (cells 11, 45) |
| **Ash-throated Flycatcher** | No Myiarchus-type bird; cell 47 is a small Empidonax/pewee |
| **Allen's Hummingbird** | Only Anna's-type hummers (cells 3, 46); Allen's rufous gorget not depicted |
| **Orange-crowned Warbler** | Cell 48 shows Wilson's-type warbler; cell 16 shows OC-type but is filed as Wilson's |

Partially present but wrong slug / wrong subspecies (fix via swap or regen):

- **Brown Pelican** — painted at **cell 27**, filed as `pacific_slope_flycatcher`
- **Western Kingbird** — painted at **cell 51** (extra)
- **Western Bluebird** — **cell 52** shows Eastern Bluebird (CLOSE)
- **Common Yellowthroat, Caspian Tern, Great Egret, Great Blue Heron, Rock Pigeon, California/Western Gull** — all in **cells 51–58** extras, not under their rank slugs

### Duplicates on sheet

| Species | Cells |
|---------|-------|
| Northern Mockingbird | 29, 32 |
| Barn Swallow | 11, 45 |
| Anna's Hummingbird | 3, 46 |
| American Crow / crow-like | 7, 20, 21 (plus ambiguous `common_raven`) |

### Suggested remapping (swap existing art — no regen)

Many rank-33+ birds are painted correctly in **later cells** but saved under the wrong slug. High-confidence swaps:

| Target slug | Copy art from cell | Painted species |
|-------------|-------------------|-----------------|
| `spotted_towhee` | 33 | Spotted Towhee |
| `brewers_blackbird` | 34 | Brewer's Blackbird |
| `pacific_slope_flycatcher` | 35 | Empidonax flycatcher (Pacific-slope) |
| `double_crested_cormorant` | 36 | Cormorant |
| `red_winged_blackbird` | 37 | Red-winged Blackbird |
| `bewicks_wren` | 38 | Bewick's/Carolina Wren |
| `brown_headed_cowbird` | 40 | Brown-headed Cowbird |
| `acorn_woodpecker` | 41 | Acorn Woodpecker |
| `killdeer` | 42 | Killdeer |
| `black_headed_grosbeak` | 43 | Black-headed Grosbeak |
| `house_sparrow` | 44 | House Sparrow |
| `barn_swallow` | 45 | Barn Swallow |
| `annas_hummingbird` | 46 | Anna's Hummingbird (duplicate of cell 3) |
| `wilsons_warbler` | 48 | Wilson's Warbler |
| `bullocks_oriole` | 49 | Bullock's Oriole |
| `american_coot` | 50 | American Coot |
| `western_kingbird` | 51 | Western Kingbird |
| `western_bluebird` | 52 | Eastern Bluebird (CLOSE stand-in) |
| `common_yellowthroat` | 53 | Common Yellowthroat |
| `caspian_tern` | 54 | Caspian Tern |
| `great_egret` | 55 | Great Egret |
| `great_blue_heron` | 56 | Great Blue Heron |
| `rock_pigeon` | 57 | Rock Pigeon |
| `california_gull` | 58 | Herring/Western-type gull |

Additional swaps for rows 4–5 misalignment:

| Target slug | Copy from | Notes |
|-------------|-----------|-------|
| `brown_pelican` | 27 | Brown Pelican misplaced at rank 27 |
| `oak_titmouse` | 25 | Oak Titmouse painted at cell 25 |
| `northern_mockingbird` | 29 or 32 | Mockingbird |
| `lesser_goldfinch` | 28 | American Goldfinch male (CLOSE) or regen |

---

## Detail table

| Rank | Slug | Expected | Actual | Status | Notes |
|-----:|------|----------|--------|--------|-------|
| 1 | mourning_dove | Mourning Dove | Mourning Dove | OK | |
| 2 | house_finch | House Finch | House Finch | OK | |
| 3 | annas_hummingbird | Anna's Hummingbird | Anna's Hummingbird | OK | |
| 4 | turkey_vulture | Turkey Vulture | Turkey Vulture | OK | |
| 5 | california_towhee | California Towhee | Spotted/Eastern Towhee | WRONG | Dark hood + rufous flanks; plain brown CA Towhee not on sheet |
| 6 | california_scrub_jay | California Scrub-Jay | California Scrub-Jay | OK | |
| 7 | common_raven | Common Raven | American Crow | CLOSE | Crow-sized; no wedge tail / shaggy throat |
| 8 | western_gull | Western Gull | Ring-billed/Herring Gull | CLOSE | Generic large gull with red bill spot |
| 9 | black_phoebe | Black Phoebe | Black Phoebe | OK | Belly slightly dark vs field guide |
| 10 | european_starling | European Starling | European Starling | OK | |
| 11 | cliff_swallow | Cliff Swallow | Barn Swallow | CLOSE | Forked tail, rufous throat; no Cliff square tail/buff rump |
| 12 | eurasian_collared_dove | Eurasian Collared-Dove | Eurasian Collared-Dove | OK | |
| 13 | song_sparrow | Song Sparrow | Song Sparrow | OK | |
| 14 | mallard | Mallard | Mallard (drake) | OK | |
| 15 | california_quail | California Quail | California Quail | OK | |
| 16 | wilsons_warbler | Wilson's Warbler | Orange-crowned Warbler | WRONG | Olive back, yellow underparts, rufous crown patch; Wilson's needs black cap |
| 17 | red_tailed_hawk | Red-tailed Hawk | Red-tailed Hawk | OK | |
| 18 | nuttalls_woodpecker | Nuttall's Woodpecker | Downy Woodpecker | CLOSE | Small B&W woodpecker; lacks Nuttall's black back/white stripes |
| 19 | american_crow | American Crow | Woodpecker (Flicker/Downy type) | WRONG | Not a corvid |
| 20 | wrentit | Wrentit | American Crow | WRONG | Wrentit not found elsewhere on sheet |
| 21 | bushtit | Bushtit | American Crow | WRONG | Not a tiny gray flocking bird |
| 22 | brown_pelican | Brown Pelican | Bewick's Wren | WRONG | Pelican painted at cell 27 instead |
| 23 | lesser_goldfinch | Lesser Goldfinch | Unclear small passerine | UNCERTAIN | Gray head, buff breast; not yellow/black Lesser Goldfinch |
| 24 | northern_mockingbird | Northern Mockingbird | Magnolia-type warbler | WRONG | Mockingbirds at cells 29, 32 |
| 25 | spotted_towhee | Spotted Towhee | Bushtit / gnatcatcher | WRONG | Spotted Towhee painted at cell 33 |
| 26 | brewers_blackbird | Brewer's Blackbird | Mountain Chickadee | WRONG | Brewer's at cell 34 |
| 27 | pacific_slope_flycatcher | Pacific-slope Flycatcher | Brown Pelican | WRONG | Flycatcher at cell 35 |
| 28 | double_crested_cormorant | Double-crested Cormorant | American Goldfinch (male) | WRONG | Cormorant at cell 36 |
| 29 | red_winged_blackbird | Red-winged Blackbird | Northern Mockingbird | WRONG | RW Blackbird at cell 37 |
| 30 | bewicks_wren | Bewick's Wren | Northern Mockingbird | WRONG | Wren at cell 38 |
| 31 | oak_titmouse | Oak Titmouse | Flycatcher w/ rufous crown | WRONG | Oak Titmouse at cell 25 |
| 32 | brown_headed_cowbird | Brown-headed Cowbird | Northern Mockingbird | WRONG | Cowbird at cell 40 |
| 33 | acorn_woodpecker | Acorn Woodpecker | Spotted Towhee | WRONG | Acorn WP at cell 41 |
| 34 | killdeer | Killdeer | Brewer's Blackbird | WRONG | Killdeer at cell 42 |
| 35 | black_headed_grosbeak | Black-headed Grosbeak | Empidonax flycatcher | WRONG | Grosbeak at cell 43 |
| 36 | house_sparrow | House Sparrow | Cormorant | WRONG | House Sparrow at cell 44 |
| 37 | barn_swallow | Barn Swallow | Red-winged Blackbird | WRONG | Barn Swallow at cell 45 |
| 38 | allens_hummingbird | Allen's Hummingbird | Bewick's Wren | WRONG | Anna's-type hummer at cell 46 |
| 39 | ash_throated_flycatcher | Ash-throated Flycatcher | Tufted Titmouse | WRONG | No Myiarchus on sheet |
| 40 | orange_crowned_warbler | Orange-crowned Warbler | Brown-headed Cowbird | WRONG | OC-type at cell 16 (wrong slug) |
| 41 | bullocks_oriole | Bullock's Oriole | Acorn Woodpecker | WRONG | Oriole at cell 49 |
| 42 | american_coot | American Coot | Killdeer | WRONG | Coot at cell 50 |
| 43 | western_kingbird | Western Kingbird | Black-headed Grosbeak | WRONG | Kingbird at cell 51 |
| 44 | western_bluebird | Western Bluebird | House Sparrow | WRONG | Eastern Bluebird at cell 52 |
| 45 | common_yellowthroat | Common Yellowthroat | Barn Swallow | WRONG | Yellowthroat at cell 53 |
| 46 | caspian_tern | Caspian Tern | Anna's Hummingbird | WRONG | Tern at cell 54 |
| 47 | great_egret | Great Egret | Empidonax / pewee | WRONG | Egret at cell 55 |
| 48 | great_blue_heron | Great Blue Heron | Wilson's Warbler | WRONG | Heron at cell 56 |
| 49 | rock_pigeon | Rock Pigeon | Bullock's Oriole | WRONG | Pigeon at cell 57 |
| 50 | california_gull | California Gull | American Coot | WRONG | Gull at cell 58 |

---

## Extras (cells 51–58)

| Cell | Actual species | Notes |
|-----:|----------------|-------|
| 51 | Western Kingbird | Gray head, yellow belly; matches rank 43 |
| 52 | Eastern Bluebird | CLOSE for Western Bluebird (rank 44) |
| 53 | Common Yellowthroat | Black mask, yellow throat; matches rank 45 |
| 54 | Caspian Tern | Orange bill, black cap; matches rank 46 |
| 55 | Great Egret | All white, yellow bill; matches rank 47 |
| 56 | Great Blue Heron | Blue-gray, S-neck; matches rank 48 |
| 57 | Rock Pigeon | Blue-barred feral pigeon; matches rank 49 |
| 58 | Herring/Western Gull | White head, gray mantle, yellow bill; matches rank 50 |

Cells 59–64 are empty on the source sheet.

---

## Recommended next steps

1. **Do not rename yet without a swap plan** — many files need content from a *different* cell, not just a rename of the current PNG.
2. **Phase 1 — re-chop or copy from correct cells** for the 24 high-confidence remappings listed above (cells 25–58 → correct slugs). This fixes ranks 22–50 for most species without regen.
3. **Phase 2 — regen only** species with no sheet art: **California Towhee**, **Wrentit**, **Cliff Swallow**, **Ash-throated Flycatcher**, **Allen's Hummingbird** (or accept Anna's as substitute with CLOSE label).
4. **Fix rank 16 vs 40 warbler confusion** — cell 16 OC-type → `orange_crowned_warbler`; regen or find Wilson's (cell 48 has Wilson's-type).
5. **Resolve row 4 mess (ranks 19–32)** — several cells contain chickadees, waxwings, phoebes, and duplicate mockers; manual mapping table needed before batch rename.
6. **Update `realistic-sprites-grid-legend.md`** after remapping to record actual species per cell.
7. **Dedupe** — pick one mockingbird, one barn swallow, one Anna's hummer; drop or overwrite duplicates.

---

*Audit completed without modifying files in `realistic-sprites/`.*
