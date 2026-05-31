"""Bird-sound identification for the bird-id prototype.

Wraps Cornell Lab's BirdNET model (run locally via `birdnetlib`) behind a single
`identify(wav_path)` function. This module never touches the microphone — it only
takes a wav file in and returns detections out — so it can be tested in a tight
loop against a fixed local file.

The clean `identify()` boundary means the BirdNET backend could later be swapped
for a hosted HTTP API without changing any callers.
"""

from __future__ import annotations

# Quiet TensorFlow's startup chatter before it is imported by birdnetlib.
import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

# Building the Analyzer loads the model + label list, which is slow. Cache it so
# repeated identify() calls (the test loop) reuse one instance.
_ANALYZER = None


def _get_analyzer():
    global _ANALYZER
    if _ANALYZER is None:
        from birdnetlib.analyzer import Analyzer

        _ANALYZER = Analyzer()
    return _ANALYZER


@dataclass
class Detection:
    common_name: str
    scientific_name: str
    confidence: float
    start_time: float
    end_time: float


def identify(
    wav_path: str | Path,
    *,
    min_conf: float = 0.3,  # mirrors config.DEFAULTS["min_conf"]
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    when: Optional[date_cls] = None,
) -> list[Detection]:
    """Identify birds in a wav file using BirdNET."""
    wav_path = Path(wav_path).expanduser()
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    from birdnetlib import Recording

    analyzer = _get_analyzer()
    kwargs: dict = {"min_conf": min_conf}
    if lat is not None and lon is not None:
        kwargs["lat"] = lat
        kwargs["lon"] = lon
        kwargs["date"] = when or date_cls.today()

    recording = Recording(analyzer, str(wav_path), **kwargs)
    recording.analyze()

    detections = [
        Detection(
            common_name=d["common_name"],
            scientific_name=d["scientific_name"],
            confidence=float(d["confidence"]),
            start_time=float(d["start_time"]),
            end_time=float(d["end_time"]),
        )
        for d in recording.detections
    ]
    detections.sort(key=lambda d: d.confidence, reverse=True)
    return detections


@dataclass
class SpeciesSummary:
    common_name: str
    scientific_name: str
    count: int            # number of 3s windows this species was detected in
    max_confidence: float
    first_time: float     # earliest detection start (s)
    last_time: float      # latest detection end (s)


def summarize(detections: list[Detection]) -> list[SpeciesSummary]:
    """Collapse per-window detections into one row per species.

    BirdNET emits a detection for every 3s window, so a long recording yields
    many near-duplicate rows per bird. This rolls them up: count of windows,
    peak confidence, and the time span over which the species was heard.
    Sorted by peak confidence, highest first.
    """
    by_species: dict[tuple[str, str], SpeciesSummary] = {}
    for d in detections:
        key = (d.common_name, d.scientific_name)
        s = by_species.get(key)
        if s is None:
            by_species[key] = SpeciesSummary(
                common_name=d.common_name,
                scientific_name=d.scientific_name,
                count=1,
                max_confidence=d.confidence,
                first_time=d.start_time,
                last_time=d.end_time,
            )
        else:
            s.count += 1
            s.max_confidence = max(s.max_confidence, d.confidence)
            s.first_time = min(s.first_time, d.start_time)
            s.last_time = max(s.last_time, d.end_time)
    return sorted(by_species.values(), key=lambda s: s.max_confidence, reverse=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Identify birds in a wav file with BirdNET.")
    parser.add_argument("wav", help="path to the audio file")
    parser.add_argument("-c", "--min-conf", type=float, default=0.3, help="min confidence 0-1")
    parser.add_argument("--lat", type=float, help="latitude for seasonal species filter")
    parser.add_argument("--lon", type=float, help="longitude for seasonal species filter")
    args = parser.parse_args()

    print(f"Analyzing {args.wav} (min_conf={args.min_conf}) ...")
    results = identify(args.wav, min_conf=args.min_conf, lat=args.lat, lon=args.lon)
    if not results:
        print("No birds detected above the confidence threshold.")
    else:
        for d in results:
            print(
                f"  {d.common_name} ({d.scientific_name})  "
                f"conf={d.confidence:.3f}  [{d.start_time:.0f}-{d.end_time:.0f}s]"
            )
        print(f"{len(results)} detection(s).")
