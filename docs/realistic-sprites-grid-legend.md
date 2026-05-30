# Realistic sprite sheet — 8×8 grid legend

Source image: `Gemini_Generated_Image_2nuqha2nuqha2nuq.png` (2048×2048)  
Output dir: `realistic-sprites/<slug>.png`  
Species list: [`santa-barbara-top-birds.md`](santa-barbara-top-birds.md)

## Layout

- **8 columns × 8 rows = 64 cells** (256×256 px each)
- **Read left → right, top → bottom** (row-major)
- **Cells 1–50** map to the top-50 Santa Barbara birds, in rank order
- **Cells 51–58** contain extra birds in the Gemini sheet (not in the top-50 list)
- **Cells 59–64** are empty

## Grid

| Row | Col 1 | Col 2 | Col 3 | Col 4 | Col 5 | Col 6 | Col 7 | Col 8 |
|-----|-------|-------|-------|-------|-------|-------|-------|-------|
| **1** | 1 · Mourning Dove<br>`mourning_dove` | 2 · House Finch<br>`house_finch` | 3 · Anna's Hummingbird<br>`annas_hummingbird` | 4 · Turkey Vulture<br>`turkey_vulture` | 5 · California Towhee<br>`california_towhee` | 6 · California Scrub-Jay<br>`california_scrub_jay` | 7 · Common Raven<br>`common_raven` | 8 · Western Gull<br>`western_gull` |
| **2** | 9 · Black Phoebe<br>`black_phoebe` | 10 · European Starling<br>`european_starling` | 11 · Cliff Swallow<br>`cliff_swallow` | 12 · Eurasian Collared-Dove<br>`eurasian_collared_dove` | 13 · Song Sparrow<br>`song_sparrow` | 14 · Mallard<br>`mallard` | 15 · California Quail<br>`california_quail` | 16 · Wilson's Warbler<br>`wilsons_warbler` |
| **3** | 17 · Red-tailed Hawk<br>`red_tailed_hawk` | 18 · Nuttall's Woodpecker<br>`nuttalls_woodpecker` | 19 · American Crow<br>`american_crow` | 20 · Wrentit<br>`wrentit` | 21 · Bushtit<br>`bushtit` | 22 · Brown Pelican<br>`brown_pelican` | 23 · Lesser Goldfinch<br>`lesser_goldfinch` | 24 · Northern Mockingbird<br>`northern_mockingbird` |
| **4** | 25 · Spotted Towhee<br>`spotted_towhee` | 26 · Brewer's Blackbird<br>`brewers_blackbird` | 27 · Pacific-slope Flycatcher<br>`pacific_slope_flycatcher` | 28 · Double-crested Cormorant<br>`double_crested_cormorant` | 29 · Red-winged Blackbird<br>`red_winged_blackbird` | 30 · Bewick's Wren<br>`bewicks_wren` | 31 · Oak Titmouse<br>`oak_titmouse` | 32 · Brown-headed Cowbird<br>`brown_headed_cowbird` |
| **5** | 33 · Acorn Woodpecker<br>`acorn_woodpecker` | 34 · Killdeer<br>`killdeer` | 35 · Black-headed Grosbeak<br>`black_headed_grosbeak` | 36 · House Sparrow<br>`house_sparrow` | 37 · Barn Swallow<br>`barn_swallow` | 38 · Allen's Hummingbird<br>`allens_hummingbird` | 39 · Ash-throated Flycatcher<br>`ash_throated_flycatcher` | 40 · Orange-crowned Warbler<br>`orange_crowned_warbler` |
| **6** | 41 · Bullock's Oriole<br>`bullocks_oriole` | 42 · American Coot<br>`american_coot` | 43 · Western Kingbird<br>`western_kingbird` | 44 · Western Bluebird<br>`western_bluebird` | 45 · Common Yellowthroat<br>`common_yellowthroat` | 46 · Caspian Tern<br>`caspian_tern` | 47 · Great Egret<br>`great_egret` | 48 · Great Blue Heron<br>`great_blue_heron` |
| **7** | 49 · Rock Pigeon<br>`rock_pigeon` | 50 · California Gull<br>`california_gull` | 51 · *(extra)* | 52 · *(extra)* | 53 · *(extra)* | 54 · *(extra)* | 55 · *(extra)* | 56 · *(extra)* |
| **8** | 57 · *(extra)* | 58 · *(extra)* | 59 · *(empty)* | 60 · *(empty)* | 61 · *(empty)* | 62 · *(empty)* | 63 · *(empty)* | 64 · *(empty)* |

## Chop status

| Row | Cells | Exported | Verified |
|-----|-------|----------|----------|
| 1 | 1–8 | yes | yes |
| 2 | 9–16 | yes | yes |
| 3 | 17–24 | yes | yes |
| 4 | 25–32 | yes | pending |
| 5 | 33–40 | yes | pending |
| 6 | 41–48 | yes | pending |
| 7 | 49–50 | yes | pending |

> **Note:** Gemini did not always paint the correct species in each cell. All 50 sprites are chopped; species-to-cell mapping still needs verification (rows 4–7 especially).

## Cell index formula

For bird rank `n` (1–50):

```
row = (n - 1) // 8        # 0-based row
col = (n - 1) % 8         # 0-based col
cell = n                  # 1-based cell number
```

Crop bounds (2048×2048 sheet, 1 px inset for grid lines):

```
x0 = col * 256 + 1
y0 = row * 256 + 1
x1 = (col + 1) * 256 - 1
y1 = (row + 1) * 256 - 1
```
