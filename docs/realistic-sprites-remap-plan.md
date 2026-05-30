# Realistic sprites — remapping plan

Maps each **top-50 slug** ([`santa-barbara-top-birds.md`](santa-barbara-top-birds.md)) to the best **source cell** on the Gemini sheet ([`realistic-sprites-gemini-legend.md`](realistic-sprites-gemini-legend.md)).

**Current chop assumption (wrong):** cell number = BirdNET rank → `realistic-sprites/<slug>.png`  
**Correct approach:** chop cell **N** → `realistic-sprites/<slug>.png` per table below.

Confidence: **HIGH** = exact or unambiguous match; **MED** = acceptable substitute (same genus/family or Gemini hedged); **REGEN** = no suitable art on sheet.

---

## Summary

| Confidence | Count | Action |
|------------|------:|--------|
| HIGH | 39 | Re-chop from listed cell |
| MED | 6 | Re-chop substitute; note in dashboard or regen later |
| REGEN | 5 | Generate new single-cell art |

---

## Remap table (authoritative)

| Rank | Slug | Source cell | Gemini cell content | Conf | Notes |
|-----:|------|------------:|---------------------|------|-------|
| 1 | `mourning_dove` | 1 | Mourning Dove | HIGH | Already correct if chopped from cell 1 |
| 2 | `house_finch` | 2 | House Finch (male) | HIGH | |
| 3 | `annas_hummingbird` | 3 | Anna's Hummingbird | HIGH | |
| 4 | `turkey_vulture` | 4 | Turkey Vulture | HIGH | |
| 5 | `california_towhee` | — | — | REGEN | Cell 5 is Dark-eyed Junco |
| 6 | `california_scrub_jay` | 6 | California Scrub-Jay | HIGH | |
| 7 | `common_raven` | 21 | Common Raven | HIGH | Prefer over ambiguous cell 7 |
| 8 | `western_gull` | 58 | California / Western Gull | HIGH | Cell 8 (Ring-billed) is MED backup |
| 9 | `black_phoebe` | 9 | Black Phoebe | HIGH | |
| 10 | `european_starling` | 10 | European Starling | HIGH | |
| 11 | `cliff_swallow` | — | — | REGEN | Barn Swallow at 11 & 45 only |
| 12 | `eurasian_collared_dove` | 12 | Eurasian Collared-Dove | HIGH | |
| 13 | `song_sparrow` | 13 | Song Sparrow | HIGH | |
| 14 | `mallard` | 14 | Mallard | HIGH | |
| 15 | `california_quail` | 15 | California Quail | HIGH | |
| 16 | `wilsons_warbler` | — | — | REGEN | Cell 16 is Yellow Warbler |
| 17 | `red_tailed_hawk` | 17 | Red-tailed Hawk | HIGH | |
| 18 | `nuttalls_woodpecker` | **53** (sheet 2) | Nuttall's / Downy | HIGH | Sheet 1 cells 18–19 wrong (Downy / spotted belly) |
| 19 | `american_crow` | 20 | American Crow | HIGH | |
| 20 | `wrentit` | — | — | REGEN | Not on sheet |
| 21 | `bushtit` | 25 | Bushtit | HIGH | |
| 22 | `brown_pelican` | 27 | Brown Pelican | HIGH | |
| 23 | `lesser_goldfinch` | — | — | REGEN | Cell 28 is American Goldfinch |
| 24 | `northern_mockingbird` | 30 | Northern Mockingbird | HIGH | Cell 32 is duplicate profile |
| 25 | `spotted_towhee` | 33 | Spotted Towhee | HIGH | |
| 26 | `brewers_blackbird` | 34 | Brewer's Blackbird | HIGH | |
| 27 | `pacific_slope_flycatcher` | 35 | Pacific-slope Flycatcher | HIGH | |
| 28 | `double_crested_cormorant` | 36 | Double-crested Cormorant | HIGH | |
| 29 | `red_winged_blackbird` | 37 | Red-winged Blackbird | HIGH | |
| 30 | `bewicks_wren` | 38 | Bewick's Wren | HIGH | |
| 31 | `oak_titmouse` | 23 | Oak Titmouse | HIGH | Cell 39 is Oak/Tufted (MED backup) |
| 32 | `brown_headed_cowbird` | 40 | Brown-headed Cowbird | HIGH | |
| 33 | `acorn_woodpecker` | 41 | Acorn Woodpecker | HIGH | |
| 34 | `killdeer` | 42 | Killdeer | HIGH | |
| 35 | `black_headed_grosbeak` | — | — | REGEN | Cell 43 is Bullock's Oriole |
| 36 | `house_sparrow` | 44 | House Sparrow | HIGH | |
| 37 | `barn_swallow` | 11 | Barn Swallow | HIGH | Cell 45 is alternate pose |
| 38 | `allens_hummingbird` | 46 | Allen's / Rufous | MED | Gemini hedged; no Anna's duplicate |
| 39 | `ash_throated_flycatcher` | — | — | REGEN | Cell 47 is Western Wood-Pewee |
| 40 | `orange_crowned_warbler` | 48 | Orange-crowned Warbler | HIGH | |
| 41 | `bullocks_oriole` | 43 | Bullock's Oriole | HIGH | Not cell 49 (Hooded/Baltimore) |
| 42 | `american_coot` | 50 | American Coot | HIGH | |
| 43 | `western_kingbird` | 51 | Western Kingbird | HIGH | Cell 24 also labeled kingbird |
| 44 | `western_bluebird` | 52 | Western Bluebird | HIGH | |
| 45 | `common_yellowthroat` | 53 | Common Yellowthroat | HIGH | |
| 46 | `caspian_tern` | 54 | Forster's / Elegant Tern | MED | Large orange-billed tern; regen ideal |
| 47 | `great_egret` | 55 | Great Egret | HIGH | |
| 48 | `great_blue_heron` | 56 | Great Blue Heron | HIGH | |
| 49 | `rock_pigeon` | 57 | Rock Pigeon | HIGH | |
| 50 | `california_gull` | 58 | California / Western Gull | HIGH | Same cell as rank 8 if both needed |

---

## MED substitutes (optional until regen)

| Slug | Cell | Substitute | Why acceptable |
|------|-----:|------------|----------------|
| `cliff_swallow` | 11 or 45 | Barn Swallow | Same genus, similar silhouette; rank 37 already uses Barn |
| `lesser_goldfinch` | 28 | American Goldfinch | Same family; SB has both |
| `allens_hummingbird` | 46 | Allen's/Rufous | Gemini combined; rufous/allen's field marks overlap |
| `caspian_tern` | 54 | Forster's/Elegant Tern | Large crested tern with orange bill |
| `western_gull` | 8 | Ring-billed / Western | Generic large gull |
| `nuttalls_woodpecker` | 18 | Downy Woodpecker | Only if cell 19 art is poor |

---

## REGEN list (5 required)

1. `california_towhee` — plain brown, long tail, no hood  
2. `wrentit` — gray-brown, rusty undertail, cocked tail  
3. `cliff_swallow` — square tail, buff rump, dark breast band (unless Barn substitute OK)  
4. `wilsons_warbler` — yellow face, **black** cap (not Yellow Warbler)  
5. `black_headed_grosbeak` — black head, orange breast, heavy bill  

Optional regen (MED slots): `lesser_goldfinch`, `ash_throated_flycatcher`, `caspian_tern`

---

## Next step: re-chop script

When ready, re-chop from source sheet using this mapping (not rank = cell):

```python
# slug -> 1-based cell number (None = skip / regen later)
REMAP = {
    "mourning_dove": 1,
    "house_finch": 2,
    # ... full dict from table above
    "california_towhee": None,
    "wrentit": None,
    # etc.
}
```

Do **not** rename existing PNGs in place without re-chopping — file bytes must come from the mapped cell.
