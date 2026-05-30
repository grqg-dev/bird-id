# Gemini supplement sheet — 12×6 legend

Source: `Gemini_Generated_Image_t9fs08t9fs08t9fs.png` (2816×1536)  
Grid: **12 columns × 6 rows** (72 cells); cells 59–72 empty in practice (row 8 cols 3–8 empty per Gemini — only 58 birds painted).

Cell size: 234×256 px (8 px horizontal margin split evenly). Chop uses 1 px inset inside grid lines.

## Legend (Gemini)

### Row 1
| Col | Species |
|----:|---------|
| 1 | California Quail |
| 2 | White-breasted Nuthatch |
| 3 | White-breasted Nuthatch (duplicate) |
| 4 | Blue-gray Gnatcatcher |
| 5 | Warbling Vireo |
| 6 | Yellow Warbler (female) |
| 7 | Lazuli Bunting / Western Bluebird variant |
| 8 | Purple Finch (orange morph) |
| 9 | Bushtit |
| 10 | Painted Bunting (immature/female) |
| 11 | Allen's Hummingbird (female/immature) |
| 12 | Western Tanager (female) |

### Row 2
| Col | Species |
|----:|---------|
| 1 | California Towhee |
| 2 | California Towhee (duplicate) |
| 3 | Wrentit |
| 4 | Canyon Wren |
| 5 | Bewick's Wren |
| 6 | Sagebrush Sparrow |
| 7 | Western Bluebird (male) |
| 8 | Purple Finch (male) |
| 9 | House Finch (male) |
| 10 | Song Sparrow |
| 11 | Black-throated Sparrow |
| 12 | California Towhee (duplicate) |

### Row 3
| Col | Species |
|----:|---------|
| 1 | Pacific-slope Flycatcher |
| 2 | Western Wood-Pewee |
| 3 | Say's Phoebe |
| 4 | Cliff Swallow *(legend)* |
| 5 | Barn Swallow *(legend)* |
| 6 | Bullock's Oriole (male) |
| 7 | Western Bluebird (duplicate) |
| 8 | Purple Finch (duplicate) |
| 9 | American Goldfinch (male) |
| 10 | Lesser Goldfinch (male) *(legend)* |
| 11 | Orange-crowned Warbler |
| 12 | Warbling Vireo (duplicate) |

### Row 4
| Col | Species |
|----:|---------|
| 1 | Vermilion Flycatcher / Summer Tanager |
| 2 | Indigo Bunting |
| 3 | Painted Bunting (male) |
| 4 | Painted Bunting (duplicate) |
| 5 | Nashville Warbler |
| 6 | Allen's Hummingbird (male) |
| 7 | Allen's Hummingbird (duplicate) |
| 8 | Ruby-crowned Kinglet |
| 9 | Cedar Waxwing (immature) |
| 10 | Western Bluebird (female) |
| 11 | Western Bluebird (female duplicate) |
| 12 | Song Sparrow (duplicate) |

### Row 5
| Col | Species |
|----:|---------|
| 1 | Ash-throated Flycatcher |
| 2 | Northern Flicker (Red-shafted) |
| 3 | American Robin |
| 4 | Western Bluebird (male duplicate) |
| 5 | Nuttall's / Downy Woodpecker |
| 6 | House Wren |
| 7 | American Kestrel (male) |
| 8 | American Kestrel (female) |
| 9 | Song Sparrow (variant) |
| 10 | Lark Sparrow |
| 11 | Song Sparrow (duplicate) |
| 12 | Yellow-rumped Warbler (female) |

### Row 6
| Col | Species |
|----:|---------|
| 1 | Vermilion Flycatcher (male) |
| 2 | Painted Whitestart |
| 3 | Rock Wren |
| 4 | Townsend's Warbler |
| 5 | Wilson's Warbler *(legend)* |
| 6 | MacGillivray's Warbler |
| 7 | Common Yellowthroat (male) |
| 8 | Yellow-breasted Chat |
| 9 | Hermit Thrush |
| 10 | White-crowned Sparrow |
| 11 | Yellow-rumped Warbler (Audubon's male) |
| 12 | Chipping Sparrow |

## Cell index

```
cell = (row - 1) * 12 + col     # row, col are 1-based
x0 = margin_x + (col - 1) * 234 + 1
y0 = (row - 1) * 256 + 1
margin_x = (2816 - 234 * 12) // 2   # = 4
```

## Used for bird-id remap

| Slug | Cell | Notes |
|------|-----:|-------|
| `california_towhee` | 13 | R2 C1 |
| `wrentit` | 15 | R2 C3 |
| `cliff_swallow` | **29** | **R3 C5** — legend C4/C5 swapped vs painted art |
| `allens_hummingbird` | 42 | R4 C6 |
| `ash_throated_flycatcher` | 49 | R5 C1 |
| `nuttalls_woodpecker` | 53 | R5 C5 — replaces wrong sheet 1 cell 19 |

## Not extracted (bad or missing art)

| Slug | Reason |
|------|--------|
| `wilsons_warbler` | Cell 65 (R6 C5) paints a wing-barred bird, not black-capped Wilson's |
| `lesser_goldfinch` | Cells 33–34 both look like American Goldfinch |
| `black_headed_grosbeak` | Not on either sheet |
| `caspian_tern` | Not on either sheet |
