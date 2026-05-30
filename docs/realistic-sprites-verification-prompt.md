# Agent prompt: verify realistic bird sprites

You are verifying that chopped bird sprite PNGs match the species they're named for. **Do not re-chop or rename files yet** — audit first, report findings.

---

## Context

We generated a realistic bird sprite sheet with Gemini and chopped it into 50 individual PNGs for a Santa Barbara bird-ID dashboard. Each file is named by **slug** (e.g. `annas_hummingbird.png`) and is *supposed* to contain that species.

**The problem:** Gemini did not always paint the correct bird in each grid cell. The chop geometry is correct (8×8 grid, row-major), but the **filename may not match what's actually in the image**. Rows 1–3 were spot-checked and look right; rows 4–7 are suspect and the full set needs a pass.

---

## Files to use

| What | Path |
|------|------|
| Source sprite sheet | `/Users/mattdennis/Downloads/Gemini_Generated_Image_2nuqha2nuqha2nuq.png` |
| Chopped sprites (50 PNGs) | `/Users/mattdennis/bird-id/realistic-sprites/` |
| Species list (rank, name, slug) | `/Users/mattdennis/bird-id/docs/santa-barbara-top-birds.md` |
| 8×8 grid legend | `/Users/mattdennis/bird-id/docs/realistic-sprites-grid-legend.md` |

**Authoritative species list:** `santa-barbara-top-birds.md` — not `birds.json`.

---

## Grid layout (for reference)

- 2048×2048 image, **8 columns × 8 rows**, 256×256 px per cell
- Read **left → right, top → bottom** (row-major)
- Cell #1 = rank #1 (Mourning Dove), … cell #50 = rank #50 (California Gull)
- Cells 51–58 = extra birds (not in top-50); cells 59–64 = empty
- Each chopped file `<slug>.png` was taken from the cell matching its rank in the legend

---

## Your task

For **each of the 50 sprites**, visually identify the bird in the PNG and compare it to the **expected species** from the markdown table (by filename slug).

### Per sprite, record:

1. **Rank** (#1–50)
2. **Expected** — common name + slug from `santa-barbara-top-birds.md`
3. **Actual** — what bird is actually painted (best ID you can give; "uncertain" if needed)
4. **Status** — one of:
   - `OK` — clearly the right species
   - `WRONG` — clearly a different species
   - `CLOSE` — same genus/family or commonly confused species (e.g. Barn Swallow vs Cliff Swallow)
   - `UNCERTAIN` — can't tell from the art
5. **Notes** — brief reason (field marks, confusion, duplicate elsewhere on sheet, etc.)

### Also check:

- **Missing species:** Any of the top 50 that don't appear anywhere in the sheet (including cells 51–58 extras)?
- **Duplicates:** Same species painted twice under different slugs?
- **Extras pool:** For each cell 51–58, identify what's painted — might be a missing top-50 bird misplaced on the sheet

---

## How to work

1. Read `santa-barbara-top-birds.md` and `realistic-sprites-grid-legend.md`
2. Open the source sheet and/or each PNG in `realistic-sprites/`
3. Work **row by row** (8 birds at a time) so you can cross-check against the grid
4. Compare each PNG to its cell on the source sheet if filename vs content is ambiguous
5. Write results to **`docs/realistic-sprites-verification-report.md`**

---

## Output format

Create `docs/realistic-sprites-verification-report.md` with:

### Summary

- Total OK / WRONG / CLOSE / UNCERTAIN counts
- List of all WRONG slugs (action needed)
- List of missing top-50 species (if any)
- Suggested remapping: if a WRONG slug's correct art is in cell 51–58 or under another slug, note it

### Detail table

```markdown
| Rank | Slug | Expected | Actual | Status | Notes |
|-----:|------|----------|--------|--------|-------|
| 1 | mourning_dove | Mourning Dove | Mourning Dove | OK | |
| 5 | california_towhee | California Towhee | Dark-eyed Junco | WRONG | Gemini substituted; towhee may be in cell 51+ |
```

### Extras (cells 51–58)

```markdown
| Cell | Actual species | Notes |
|-----:|----------------|-------|
| 51 | … | |
```

### Recommended next steps

Bullet list for the human or a follow-up agent, e.g.:

- Rename / swap files where correct art exists elsewhere on sheet
- Regenerate specific cells with Gemini
- Update grid legend if mapping changes

---

## Rules

- **Do not rename, move, or delete** files in `realistic-sprites/` during verification
- **Do not re-chop** the grid unless explicitly asked after the report
- Use **Santa Barbara / California** field-guide thinking — these are local species
- When two species look similar (Crow vs Raven, Cliff vs Barn Swallow, Allen's vs Anna's Hummingbird), say so in Notes and use CLOSE or UNCERTAIN rather than guessing
- Rows 1–3 (#1–24) were human-verified as correct — still log them, but flag only if you disagree

---

## Known issues from prior work

- Gemini sometimes swapped species mid-sheet (e.g. California Towhee cell may show Dark-eyed Junco)
- Some list birds may only appear in the **extras** cells (51–58), not at their assigned rank
- Chop uses 1 px inset inside grid lines; geometry is not the problem — species assignment is

---

## Done when

- All 50 sprites audited
- Cells 51–58 documented
- Report written to `docs/realistic-sprites-verification-report.md`
- Summary makes it obvious which slugs need fixing and whether fixes are swaps vs regen
