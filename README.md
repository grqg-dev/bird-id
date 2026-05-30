# bird-id

**A microphone in your yard that tells you who's visiting — and keeps a journal.**

Point a mic at your backyard, leave bird-id running, and come back to a personal
**Bird-Dex**: every species you've heard, when you heard it, and a recording of
the actual call. No app store, no subscription, no sending your audio to the cloud.

![Bird-Dex — your backyard bird collection: illustrations, call playback, and stats for every species you've heard](docs/bird-dex-full.png)

![Birds identified from a Santa Barbara backyard recording — Oak Titmouse, Pacific-slope Flycatcher, American Crow, Dark-eyed Junco](docs/species-detected.png)

---

## What is this?

bird-id listens to bird sounds, figures out which species are calling, and saves
the results. Over days and weeks it becomes a running log of your yard — which
birds showed up, how often, and audio you can play back.

Think of it as a **field journal that writes itself**. The web dashboard turns
your sightings into a collection you can browse, like a pocket guide filled in
with *your* birds.

It uses Cornell Lab's BirdNET model, running entirely on your own computer. Your
recordings never leave the machine.

## Bird illustrations

The Bird-Dex cards show a realistic illustration for each common local species
(when we have one). We made them with **Google Gemini**: paste a prompt, get back
a grid of field-guide-style birds on a white background, then slice the sheet
into individual images in `realistic-sprites/`.

Gemini doesn't always paint the right bird in every cell, so some illustrations
were pulled from a second supplement sheet or generated one bird at a time. The
full Gemini prompts, species list, and chopping notes are in
[`docs/realistic-sprites-prompt.md`](docs/realistic-sprites-prompt.md).

## How it works

1. **Listen** — bird-id records audio from a microphone in short chunks.
2. **Identify** — each chunk is analyzed for bird calls; matches are saved with
   a timestamp and confidence score.
3. **Browse** — open the Bird-Dex dashboard to see your collection, play back
   calls, and check what's new today.

You can also point it at an existing recording file if you already have one.

## Get started

Requires **Python 3.12** on macOS (Apple Silicon tested).

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Copy `config.example.json` to `config.json` and set your latitude/longitude so
bird-id only considers birds that actually live near you. The included config is
set for Santa Barbara, CA.

**Try it on a recording** (no microphone needed):

```bash
./.venv/bin/python birdid.py identify ~/Desktop/bird.wav
```

**Run it continuously** (the main idea — leave it going):

```bash
./.venv/bin/python birdid.py monitor
./.venv/bin/python birdid.py dashboard    # → http://127.0.0.1:8080
```

The dashboard works offline — no internet required once it's running.

Gallery mode (`/?mode=gallery`) is a lighter grid of illustrations and stats — no
spectrograms or audio:

![Bird-Dex gallery view](docs/bird-dex-gallery.png)

## Daily digest

```bash
./.venv/bin/python birdid.py digest
```

A quick terminal summary: how many species today, busiest hour, and anything
**new to your records**.

---

## More commands

| What you want | Command |
|---|---|
| Record a few seconds from the mic | `./.venv/bin/python birdid.py record out.wav -t 5` |
| Record + identify in one step | `./.venv/bin/python birdid.py listen -t 5` |
| Running tally while monitoring | `./.venv/bin/python birdid.py stats` |
| Long file, one row per species | `./.venv/bin/python birdid.py identify big.wav --summary` |
| Dashboard on your home network | `./.venv/bin/python birdid.py dashboard --host 0.0.0.0` |

**Useful flags:** `-c 0.5` raises the confidence threshold (fewer false positives).
`-d 1` picks a specific mic — list devices with `./.venv/bin/python recorder.py --list-devices`.

If recording comes back silent, check macOS mic permission:
*System Settings → Privacy & Security → Microphone → enable your terminal app.*

## Configuration

`config.json` holds your location, confidence threshold, and where data is stored:

```json
{ "lat": 34.4208, "lon": -119.6982, "min_conf": 0.3,
  "db": "birdid.db", "recordings_dir": "recordings" }
```

Setting location is the single biggest accuracy win — it stops the model from
suggesting birds that don't belong in your area or season.

## Project layout

For contributors and the curious — see `AGENTS.md` for design notes.

| File | What it does |
|---|---|
| `birdid.py` | CLI — the commands above |
| `dashboard.py` | Bird-Dex web UI |
| `recorder.py` | Microphone → audio file (macOS) |
| `identifier.py` | Audio file → bird detections |
| `storage.py` | SQLite database and queries |
| `config.py` | Loads `config.json` |

## Roadmap

- [x] Continuous backyard monitoring with audio playback
- [x] Bird-Dex web dashboard with illustrations and call clips
- [x] Daily digest and "new species" tracking
- [x] Location/season filtering
- [ ] Always-on setup on a Mac mini or Raspberry Pi with a USB mic
- [ ] Alerts when a rare or first-time species shows up
