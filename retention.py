"""One retention policy for per-detection clips.

`plan()` is a pure function: tracks in, keep/delete decisions out — no files,
no database, no clock. `run()` is the thin shell that loads tracks from the DB,
stats the clip files, calls `plan()`, and (unless dry-run) unlinks the files and
NULLs `tracks.clip_path`.

Policy (all numbers are config keys, see `Policy.from_config`):
  * last `live_hours` hours                -> always kept          (live_window)
  * best N clips per species that still have audio -> kept forever (species_best)
  * otherwise by the track's peak confidence:
        >= high_conf  -> keep `high_days`   (high_conf)
        >= mid_conf   -> keep `mid_days`    (mid_conf)
        below         -> keep `low_days`    (low_conf)
  * total clip bytes over `max_bytes`      -> evict lowest confidence, then
        oldest, never live_window / species_best                    (over_budget)

Segment wavs are handled elsewhere (`storage.expire_segment_audio`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence

REASONS = ("live_window", "species_best", "high_conf", "mid_conf", "low_conf", "over_budget")

# Used only by `--assume-clips-present` dry runs against a DB pulled without audio.
ASSUMED_CLIP_BYTES = 23_000


@dataclass(frozen=True)
class Policy:
    high_conf: float = 0.9
    mid_conf: float = 0.7
    high_days: int = 365
    mid_days: int = 30
    low_days: int = 7
    best_per_species: int = 10
    live_hours: int = 24
    max_bytes: int = 10_000_000_000

    @classmethod
    def from_config(cls, cfg: Mapping) -> "Policy":
        d = cls()
        g = lambda k, default: cfg.get(k, default) if cfg.get(k) is not None else default  # noqa: E731
        return cls(
            high_conf=float(g("retention_high_conf", d.high_conf)),
            mid_conf=float(g("retention_mid_conf", d.mid_conf)),
            high_days=int(g("retention_high_days", d.high_days)),
            mid_days=int(g("retention_mid_days", d.mid_days)),
            low_days=int(g("retention_low_days", d.low_days)),
            best_per_species=int(g("retention_best_per_species", d.best_per_species)),
            live_hours=int(g("retention_live_hours", d.live_hours)),
            max_bytes=int(float(g("retention_max_clip_gb", d.max_bytes / 1e9)) * 1e9),
        )


@dataclass(frozen=True)
class Track:
    """What `plan()` needs to know about one clip."""
    id: int
    when: datetime                      # segment start; ages are measured from here
    max_conf: float                     # peak confidence over the track's detections
    size: int = 0                       # clip bytes on disk
    species: tuple[tuple[str, float], ...] = ()   # (common_name, best conf in this track)
    has_audio: bool = True              # False -> ignored (cannot be kept or deleted)


@dataclass(frozen=True)
class Decision:
    track_id: int
    keep: bool
    reason: str


def plan(tracks: Iterable[Track], now: datetime, policy: Policy) -> list[Decision]:
    """Decide keep/delete for every track that still has audio. Pure."""
    live = [t for t in tracks if t.has_audio]

    # Best N per species, counting only clips that still have audio. A track
    # holding several species can rank for each of them.
    by_species: dict[str, list[tuple[float, datetime, int]]] = defaultdict(list)
    for t in live:
        for name, conf in t.species:
            by_species[name].append((conf, t.when, t.id))
    best: set[int] = set()
    if policy.best_per_species > 0:
        for entries in by_species.values():
            entries.sort(key=lambda e: (-e[0], -e[1].timestamp(), e[2]))
            best.update(e[2] for e in entries[: policy.best_per_species])

    live_cutoff = now - timedelta(hours=policy.live_hours)
    decisions: dict[int, Decision] = {}
    for t in live:
        if t.when >= live_cutoff:
            decisions[t.id] = Decision(t.id, True, "live_window")
        elif t.id in best:
            decisions[t.id] = Decision(t.id, True, "species_best")
        else:
            if t.max_conf >= policy.high_conf:
                reason, days = "high_conf", policy.high_days
            elif t.max_conf >= policy.mid_conf:
                reason, days = "mid_conf", policy.mid_days
            else:
                reason, days = "low_conf", policy.low_days
            keep = t.when >= now - timedelta(days=days)
            decisions[t.id] = Decision(t.id, keep, reason)

    # Disk budget: evict lowest confidence, then oldest, from the evictable kept set.
    if policy.max_bytes > 0:
        total = sum(t.size for t in live if decisions[t.id].keep)
        if total > policy.max_bytes:
            evictable = sorted(
                (t for t in live
                 if decisions[t.id].keep
                 and decisions[t.id].reason not in ("live_window", "species_best")),
                key=lambda t: (t.max_conf, t.when, t.id),
            )
            for t in evictable:
                if total <= policy.max_bytes:
                    break
                decisions[t.id] = Decision(t.id, False, "over_budget")
                total -= t.size

    return [decisions[t.id] for t in live]


def keep_info(
    conf: float, when: datetime, rank: int, now: datetime, policy: Policy
) -> tuple[str, datetime | None]:
    """Why a clip that exists now is kept, and until when (None = indefinitely).

    Mirrors the keep rules in `plan()` for one clip (not the disk-budget pass):
    `rank` is its 0-based position among the species' clips that still have
    audio, best first. Used by the dashboard to explain a clip's lifetime.
    """
    if when >= now - timedelta(hours=policy.live_hours):
        return "live_window", when + timedelta(hours=policy.live_hours)
    if rank < policy.best_per_species:
        return "species_best", None
    if conf >= policy.high_conf:
        return "high_conf", when + timedelta(days=policy.high_days)
    if conf >= policy.mid_conf:
        return "mid_conf", when + timedelta(days=policy.mid_days)
    return "low_conf", when + timedelta(days=policy.low_days)


def summarize(
    decisions: Sequence[Decision], sizes: Mapping[int, int]
) -> dict[str, dict[str, dict[str, int]]]:
    """{'keep'|'delete': {reason: {'count': n, 'bytes': b}}} for reporting."""
    out: dict[str, dict[str, dict[str, int]]] = {"keep": {}, "delete": {}}
    for d in decisions:
        bucket = out["keep" if d.keep else "delete"].setdefault(d.reason, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += sizes.get(d.track_id, 0)
    return out


def apply(conn, decisions: Iterable[Decision], paths: Mapping[int, str]) -> tuple[int, int]:
    """Unlink files and NULL clip_path for every keep=False decision.

    Returns (clips_deleted, bytes_freed). A file that is already gone still has
    its clip_path cleared (the decision said the audio should not exist).
    """
    deleted = freed = 0
    for d in decisions:
        if d.keep:
            continue
        p = paths.get(d.track_id)
        if p:
            path = Path(p)
            try:
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink()
            except OSError:
                continue   # leave the row so the next run retries
        conn.execute("UPDATE tracks SET clip_path = NULL WHERE id = ?", (d.track_id,))
        deleted += 1
    if deleted:
        conn.commit()
    return deleted, freed


def load_tracks(conn, *, assume_present: bool = False) -> tuple[list[Track], dict[int, str], int]:
    """Read tracks that have a clip_path and stat their files.

    Returns (tracks, paths_by_id, missing_files). Tracks whose file is missing
    get has_audio=False (they neither hold best-N slots nor get decisions) and
    are only counted — never NULLed here, since a wrong CWD makes every
    relative path look missing. `assume_present` skips the stat (dry runs
    against a DB pulled without its audio) and uses ASSUMED_CLIP_BYTES.
    """
    import storage

    rows = storage.retention_rows(conn)
    species: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for r in storage.retention_species(conn):
        species[r["track_id"]].append((r["common_name"], r["conf"]))

    tracks: list[Track] = []
    paths: dict[int, str] = {}
    missing = 0
    for r in rows:
        size = ASSUMED_CLIP_BYTES
        present = True
        if not assume_present:
            try:
                size = Path(r["clip_path"]).stat().st_size
            except OSError:
                size, present = 0, False
                missing += 1
        paths[r["id"]] = r["clip_path"]
        tracks.append(
            Track(
                id=r["id"],
                when=datetime.fromisoformat(r["started_at"]),
                max_conf=r["max_conf"] if r["max_conf"] is not None else 0.0,
                size=size,
                species=tuple(species.get(r["id"], ())),
                has_audio=present,
            )
        )
    return tracks, paths, missing


def run(
    conn,
    policy: Policy,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    assume_present: bool = False,
) -> dict:
    """Load, plan and (unless dry_run) apply. Returns a report dict."""
    now = now or datetime.now()
    tracks, paths, missing = load_tracks(conn, assume_present=assume_present)
    decisions = plan(tracks, now, policy)
    sizes = {t.id: t.size for t in tracks}
    report = {
        "considered": len(decisions),
        "missing_files": missing,
        "summary": summarize(decisions, sizes),
        "deleted": 0,
        "bytes_freed": 0,
    }
    if not dry_run:
        report["deleted"], report["bytes_freed"] = apply(conn, decisions, paths)
    return report


def format_report(report: dict, *, dry_run: bool) -> list[str]:
    """Human lines: counts and bytes per reason."""
    lines = [f"{report['considered']} clip(s) with audio considered"
             + (f", {report['missing_files']} row(s) point at missing files (left alone)"
                if report["missing_files"] else "")]
    for verdict in ("keep", "delete"):
        label = ("would keep" if dry_run else "kept") if verdict == "keep" else (
            "would delete" if dry_run else "deleted")
        bucket = report["summary"][verdict]
        for reason in REASONS:
            if reason in bucket:
                b = bucket[reason]
                lines.append(f"  {label:<12} {reason:<13} {b['count']:>7} clips  {b['bytes'] / 1e6:>9.1f} MB")
    return lines
