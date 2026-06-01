# Realistic bird sprites — Gemini prompts

Field-guide illustrations for the Bird-Dex dashboard. Generated with **Google
Gemini** image generation, then chopped into individual PNGs in
`realistic-sprites/<slug>.png`.

Species list and slugs: [`santa-barbara-top-birds.md`](santa-barbara-top-birds.md)
(machine-readable: [`birds.json`](birds.json)).

## How we made them

1. **Main sheet** — one Gemini prompt asks for an **8×8 grid** (2048×2048 px) of
   50 birds + a few extras, ranked by BirdNET likelihood for Santa Barbara.
2. **Chop** — `scripts/chop_realistic_sprites.py` slices each cell into a 254×254
   PNG, mapped by slug (see [`realistic-sprites-remap-plan.md`](realistic-sprites-remap-plan.md)
   — Gemini often paints the wrong bird in a cell, so we remap slug → cell).
3. **Supplement sheet** — a second 12×6 Gemini sheet filled gaps and wrong cells;
   chopped with `scripts/chop_realistic_sprites_sheet2.py`
   (legend: [`realistic-sprites-gemini-legend-sheet2.md`](realistic-sprites-gemini-legend-sheet2.md)).
4. **Missing-species sheet** — a small grid (2×3 or 2×4) for species still without
   PNGs after the main and 12×6 passes; chopped with
   `scripts/chop_realistic_sprites_sheet3.py` (update `REMAP` and `SOURCE` there).
5. **One-offs** — individual species (e.g. Dark-eyed Junco) regenerated with the
   single-bird prompt below when no sheet cell is usable.

Grid layout reference: [`realistic-sprites-grid-legend.md`](realistic-sprites-grid-legend.md).

---

## Prompt — main 8×8 sprite sheet (birds 1–50)

Square output, **1:1 aspect ratio**, 2048×2048. Copy-paste into Gemini.

```
A natural history field guide illustration sprite sheet: an 8×8 grid of 64 equal
square cells on a pure flat white background. Exactly one adult bird per filled
cell — no duplicates, no multiple birds in a cell. Clean digital illustration
style like a modern bird field guide: accurate plumage colors and proportions,
soft shading, thin dark outlines, not photorealistic, not pixel art, not
watercolor. Each bird is shown full-body in a characteristic side-profile pose
perched on a simple bare twig or branch, facing right, centered in its cell
with a small even margin. White gutter between cells, no visible grid lines, no
text, no labels, no numbers.

Filled cells (one bird each, left to right, top to bottom — row 1 first):

Cell 1: Mourning Dove (Zenaida macroura)
Cell 2: House Finch (Haemorhous mexicanus)
Cell 3: Anna's Hummingbird (Calypte anna)
Cell 4: Turkey Vulture (Cathartes aura)
Cell 5: California Towhee (Melozone crissalis)
Cell 6: California Scrub-Jay (Aphelocoma californica)
Cell 7: Common Raven (Corvus corax)
Cell 8: Western Gull (Larus occidentalis)
Cell 9: Black Phoebe (Sayornis nigricans)
Cell 10: European Starling (Sturnus vulgaris)
Cell 11: Cliff Swallow (Petrochelidon pyrrhonota)
Cell 12: Eurasian Collared-Dove (Streptopelia decaocto)
Cell 13: Song Sparrow (Melospiza melodia)
Cell 14: Mallard (Anas platyrhynchos)
Cell 15: California Quail (Callipepla californica)
Cell 16: Wilson's Warbler (Cardellina pusilla)
Cell 17: Red-tailed Hawk (Buteo jamaicensis)
Cell 18: Nuttall's Woodpecker (Dryobates nuttallii)
Cell 19: American Crow (Corvus brachyrhynchos)
Cell 20: Wrentit (Chamaea fasciata)
Cell 21: Bushtit (Psaltriparus minimus)
Cell 22: Brown Pelican (Pelecanus occidentalis)
Cell 23: Lesser Goldfinch (Spinus psaltria)
Cell 24: Northern Mockingbird (Mimus polyglottos)
Cell 25: Spotted Towhee (Pipilo maculatus)
Cell 26: Brewer's Blackbird (Euphagus cyanocephalus)
Cell 27: Pacific-slope Flycatcher (Empidonax difficilis)
Cell 28: Double-crested Cormorant (Nannopterum auritum)
Cell 29: Red-winged Blackbird (Agelaius phoeniceus)
Cell 30: Bewick's Wren (Thryomanes bewickii)
Cell 31: Oak Titmouse (Baeolophus inornatus)
Cell 32: Brown-headed Cowbird (Molothrus ater)
Cell 33: Acorn Woodpecker (Melanerpes formicivorus)
Cell 34: Killdeer (Charadrius vociferus)
Cell 35: Black-headed Grosbeak (Pheucticus melanocephalus)
Cell 36: House Sparrow (Passer domesticus)
Cell 37: Barn Swallow (Hirundo rustica)
Cell 38: Allen's Hummingbird (Selasphorus sasin)
Cell 39: Ash-throated Flycatcher (Myiarchus cinerascens)
Cell 40: Orange-crowned Warbler (Leiothlypis celata)
Cell 41: Bullock's Oriole (Icterus bullockii)
Cell 42: American Coot (Fulica americana)
Cell 43: Western Kingbird (Tyrannus verticalis)
Cell 44: Western Bluebird (Sialia mexicana)
Cell 45: Common Yellowthroat (Geothlypis trichas)
Cell 46: Caspian Tern (Hydroprogne caspia)
Cell 47: Great Egret (Ardea alba)
Cell 48: Great Blue Heron (Ardea herodias)
Cell 49: Rock Pigeon (Columba livia)
Cell 50: California Gull (Larus californicus)

Cells 51–64: leave empty or fill with extra Santa Barbara species if needed.
Same illustration style, pose, and framing in every cell.
```

---

## Prompt — supplement grid (missing species)

Use when a handful of slugs still have no PNG after the main 8×8 and 12×6
sheets. Pick a grid that fits (e.g. **3×3** for nine birds, **2×3** for five
birds + one empty cell). Square output, **1:1 aspect ratio**, 2048×2048. Same
style as the main sheet.

**Workflow:** list missing slugs → fill the prompt below → generate → ask Gemini
for a row-by-row cell legend → update `REMAP` in
`scripts/chop_realistic_sprites_sheet3.py` → run the chop script.

Gemini may ignore the requested grid size (we asked for 2×3 and got **2×4**).
That's fine — remap slugs to the cells that actually contain the right species
and skip wrong or duplicate cells.

### Template

Replace `[ROWS]`, `[COLS]`, and the cell list. Add one empty cell if the grid
has spare slots (helps Gemini keep even spacing).

```
A natural history field guide illustration sprite sheet: a [ROWS]×[COLS] grid of
[N] equal square cells on a pure flat white background. Exactly one adult bird
per filled cell — no duplicates, no multiple birds in a cell. Clean digital
illustration style like a modern bird field guide: accurate plumage colors and
proportions, soft shading, thin dark outlines, not photorealistic, not pixel art,
not watercolor. Each bird is shown full-body in a characteristic side-profile
pose perched on a simple bare twig or branch, facing right, centered in its cell
with a small even margin. White gutter between cells, no visible grid lines, no
text, no labels, no numbers.

Filled cells (left to right, top to bottom):

Cell 1: [COMMON NAME] ([SCIENTIFIC NAME]) — [key field marks for ID]
Cell 2: ...
Cell N: leave empty (pure white).

Same illustration style, pose, and framing in every filled cell.
```

Use the [negative prompt](#negative-prompt-if-gemini-supports-it) below.

### Current — June 2026 (nine detected, no sprite)

All top-50 catalog slugs have PNGs. These nine appear in detections but have no
`realistic-sprites/<slug>.png` yet (dashboard derives slugs automatically).

| Slug | Common name |
|------|-------------|
| `barn_owl` | Barn Owl |
| `brown_creeper` | Brown Creeper |
| `forsters_tern` | Forster's Tern |
| `great_horned_owl` | Great Horned Owl |
| `green_winged_teal` | Green-winged Teal |
| `western_grebe` | Western Grebe |
| `whimbrel` | Whimbrel |
| `white_breasted_nuthatch` | White-breasted Nuthatch |
| `white_crowned_sparrow` | White-crowned Sparrow |

Copy-paste into Gemini (**3×3** grid, 2048×2048):

```
A natural history field guide illustration sprite sheet: a 3×3 grid of 9 equal
square cells on a pure flat white background. Exactly one adult bird per cell —
no duplicates, no multiple birds in a cell. Clean digital illustration style
like a modern bird field guide: accurate plumage colors and proportions, soft
shading, thin dark outlines, not photorealistic, not pixel art, not watercolor.
Each bird is shown full-body in a characteristic side-profile pose, facing
right, centered in its cell with a small even margin. Land birds perch on a
simple bare twig or branch; waterbirds stand on ground, mudflat, or low rocky
perch as appropriate. White gutter between cells, no visible grid lines, no
text, no labels, no numbers.

Filled cells (left to right, top to bottom):

Cell 1: Barn Owl (Tyto alba) — medium owl; heart-shaped white face, dark eyes,
golden-buff upperparts, white underparts with fine gray spots, long rounded wings
Cell 2: Brown Creeper (Certhia americana) — tiny brown songbird clinging to tree
bark; mottled brown upperparts, white underparts, slender decurved bill, long
stiff tail used as prop
Cell 3: Forster's Tern (Sterna forsteri) — medium tern; breeding adult with
black cap, pale gray wings, white body, orange bill with black tip, standing on
ground or low perch
Cell 4: Great Horned Owl (Bubo virginianus) — large owl; prominent ear tufts,
barred brown and gray plumage, white throat bib, yellow eyes, perched on thick
branch
Cell 5: Green-winged Teal (Anas crecca) — small dabbling duck; male with
chestnut head, green ear patch, gray flanks, buff stripe along side, yellow
undertail coverts, standing on ground
Cell 6: Western Grebe (Aechmophorus occidentalis) — elegant waterbird; black
cap, red eye, black neck and upperparts, white underparts, long slender neck,
shown floating on calm water in side profile
Cell 7: Whimbrel (Numenius phaeopus) — large brown shorebird; long decurved
bill, striped dark-and-light head, mottled brown upperparts, pale underparts,
standing on ground
Cell 8: White-breasted Nuthatch (Sitta carolinensis) — compact songbird;
blue-gray back, white face and breast, black cap (male), rusty undertail
coverts, perched on bare twig
Cell 9: White-crowned Sparrow (Zonotrichia leucophrys) — sparrow with bold
black-and-white striped crown, plain gray breast, pink bill, brown streaked back,
perched on bare twig

Same illustration style, pose, and framing in every cell.
```

After generation: ask Gemini for a cell legend, update `COLS`/`ROWS`, `SOURCE`,
and `REMAP` in `scripts/chop_realistic_sprites_sheet3.py`, then run the chop
script.

### Previous run — May 2026 (five missing catalog / dex species)

Species: Lesser Goldfinch, Red-shouldered Hawk, Wilson's Warbler, Black-headed
Grosbeak, Caspian Tern. Gemini returned a **2×4** sheet; we kept cells 1, 4, 5,
6, and 8 (see `chop_realistic_sprites_sheet3.py`).

```
A natural history field guide illustration sprite sheet: a 2×3 grid of 6 equal
square cells on a pure flat white background. Exactly one adult bird per filled
cell — no duplicates, no multiple birds in a cell. Clean digital illustration
style like a modern bird field guide: accurate plumage colors and proportions,
soft shading, thin dark outlines, not photorealistic, not pixel art, not
watercolor. Each bird is shown full-body in a characteristic side-profile pose
perched on a simple bare twig or branch, facing right, centered in its cell
with a small even margin. White gutter between cells, no visible grid lines, no
text, no labels, no numbers.

Filled cells (left to right, top to bottom):

Cell 1: Lesser Goldfinch (Spinus psaltria) — small finch; male with black cap,
bright yellow underparts, greenish back, black wings with bold white wing bars
Cell 2: Red-shouldered Hawk (Buteo lineatus) — medium buteo; rufous-orange
breast, barred rufous belly, black-and-white checkered wings, black tail with
narrow white bands, perched not flying
Cell 3: Wilson's Warbler (Cardellina pusilla) — tiny warbler; male with neat
round black cap, bright yellow face and underparts, olive-green back
Cell 4: Black-headed Grosbeak (Pheucticus melanocephalus) — male with solid
black head, rich orange breast, black and white wings, heavy pale bill
Cell 5: Caspian Tern (Hydroprogne caspia) — large tern standing on ground or
low perch; white body, pale gray wings, shaggy black cap, long thick red-orange
bill with dark tip

Cell 6: leave empty (pure white).

Same illustration style, pose, and framing in every filled cell.
```

---

## Prompt — single bird (fixes & extras)

Use when a sheet cell is wrong or the species isn't in the top 50. Square 1:1
output; scale/pad to 254×254 to match the chopped sprites.

```
A natural history field guide illustration of [COMMON NAME] ([SCIENTIFIC NAME]),
full-body side profile perched on a simple bare twig, facing right, accurate
plumage colors and proportions, clean digital illustration with soft shading and
thin dark outlines, pure flat white background, centered with margin, no text,
no watermark, not photorealistic, not pixel art.
```

Example (Dark-eyed Junco):

```
A natural history field guide illustration of Dark-eyed Junco (Junco hyemalis),
Oregon subspecies, full-body side profile perched on a simple bare twig, facing
right, accurate plumage colors and proportions, clean digital illustration with
soft shading and thin dark outlines, pure flat white background, centered with
margin, no text, no watermark, not photorealistic, not pixel art.
```

---

## Negative prompt (if Gemini supports it)

```
no text, no labels, no captions, no species names, no numbers, no watermark,
no signature, no border or frame, no multiple birds in one cell, no duplicate
birds, no humans, no hands, no cage, no background scenery, no gradient
background, not blurry, not 3D, not pixel art, not SNES, not cartoon, no merged
or overlapping birds between cells
```

---

## After generation

- Ask Gemini for a **cell-by-cell legend** of what it actually painted (see
  [`realistic-sprites-gemini-legend.md`](realistic-sprites-gemini-legend.md)).
- Map each slug to the best cell before chopping — don't assume cell number =
  BirdNET rank.
- Re-run `./.venv/bin/python scripts/chop_realistic_sprites.py` after updating
  the `REMAP` table in that script.
- For missing-species grids, update `SOURCE` and `REMAP` in
  `scripts/chop_realistic_sprites_sheet3.py`, then run it.

Abandoned **16-bit pixel art** experiments (different prompts) live on branch
`retro-sprites` as `sprites/` — not used by the dashboard.
