"""retention.plan() rules (pure) plus the DB-facing run()/apply() shell."""

from __future__ import annotations

from datetime import datetime, timedelta

import identifier
import retention
import storage
from retention import Decision, Policy, Track

NOW = datetime(2026, 11, 11, 12, 0, 0)
P = Policy()   # D1 defaults


def trk(i, age_days, conf, size=1000, species=None, has_audio=True, hours=None):
    when = NOW - (timedelta(hours=hours) if hours is not None else timedelta(days=age_days))
    sp = species if species is not None else ((f"sp{i}", conf),)
    return Track(id=i, when=when, max_conf=conf, size=size, species=tuple(sp), has_audio=has_audio)


def by_id(decisions):
    return {d.track_id: d for d in decisions}


def test_defaults_match_decision_d1():
    assert (P.high_conf, P.mid_conf) == (0.9, 0.7)
    assert (P.high_days, P.mid_days, P.low_days) == (365, 30, 7)
    assert P.best_per_species == 10 and P.live_hours == 24 and P.max_bytes == 10_000_000_000


def test_policy_from_config():
    p = Policy.from_config({"retention_high_days": 400, "retention_max_clip_gb": 2,
                            "retention_low_days": None})
    assert p.high_days == 400 and p.max_bytes == 2_000_000_000 and p.low_days == 7


# (age_days, conf, expected keep, expected reason) — every species distinct and each
# track is the only clip of its species, so species_best would keep it; widen with
# 10 better clips per species below instead. Here best_per_species=0 isolates tiers.
TIER_CASES = [
    (100, 0.95, True, "high_conf"),
    (364, 0.90, True, "high_conf"),
    (366, 0.95, False, "high_conf"),
    (29, 0.80, True, "mid_conf"),
    (31, 0.80, False, "mid_conf"),
    (31, 0.95, True, "high_conf"),      # high tier outlives the mid cutoff
    (6, 0.50, True, "low_conf"),
    (8, 0.50, False, "low_conf"),
    (8, 0.699, False, "low_conf"),      # just below the mid threshold
    (29, 0.70, True, "mid_conf"),       # threshold is inclusive
]


def test_tier_rules():
    pol = Policy(best_per_species=0)
    tracks = [trk(i, age, conf) for i, (age, conf, _, _) in enumerate(TIER_CASES)]
    got = by_id(retention.plan(tracks, NOW, pol))
    for i, (_, _, keep, reason) in enumerate(TIER_CASES):
        assert (got[i].keep, got[i].reason) == (keep, reason), TIER_CASES[i]


def test_live_window_always_kept_even_low_confidence_over_budget():
    pol = Policy(best_per_species=0, max_bytes=1)
    d = by_id(retention.plan([trk(1, 0, 0.31, size=500, hours=23)], NOW, pol))[1]
    assert (d.keep, d.reason) == (True, "live_window")
    d = by_id(retention.plan([trk(1, 0, 0.31, hours=25)], NOW, Policy(best_per_species=0)))[1]
    assert d.keep is True and d.reason == "low_conf"   # 25 h old, but < 7 days


def test_species_best_keeps_top_n_forever():
    # 12 old low-confidence clips of one species: the best 10 survive, 2 are dropped.
    tracks = [trk(i, 200, 0.40 + i * 0.01, species=(("Wren", 0.40 + i * 0.01),)) for i in range(12)]
    got = by_id(retention.plan(tracks, NOW, Policy(best_per_species=10)))
    kept = {i for i, d in got.items() if d.keep}
    assert kept == set(range(2, 12))
    assert all(got[i].reason == "species_best" for i in kept)
    assert got[0] == Decision(0, False, "low_conf") and got[1].keep is False


def test_best_n_only_counts_clips_that_still_have_audio():
    # The 5 best Wren detections have no audio any more; they must not hold slots.
    gone = [trk(100 + i, 300, 0.99, species=(("Wren", 0.99),), has_audio=False) for i in range(5)]
    left = [trk(i, 200, 0.40, species=(("Wren", 0.40),)) for i in range(3)]
    got = by_id(retention.plan(gone + left, NOW, Policy(best_per_species=3)))
    assert set(got) == {0, 1, 2}                      # no decisions for audio-less tracks
    assert all(d.keep and d.reason == "species_best" for d in got.values())


def test_multi_species_track_can_rank_for_either_species():
    shared = trk(1, 200, 0.5, species=(("Wren", 0.5), ("Jay", 0.45)))
    better_wrens = [trk(10 + i, 200, 0.6, species=(("Wren", 0.6),)) for i in range(2)]
    got = by_id(retention.plan([shared] + better_wrens, NOW, Policy(best_per_species=1)))
    assert got[1].keep and got[1].reason == "species_best"   # best Jay
    assert got[10].keep and got[11].keep is False             # only one Wren slot


def test_budget_evicts_lowest_confidence_then_oldest_never_protected():
    pol = Policy(best_per_species=1, max_bytes=2500, high_days=365, mid_days=30, low_days=30)
    tracks = [
        trk(1, 5, 0.40, size=1000, species=(("A", 0.40),)),   # species_best (only A)
        trk(2, 5, 0.45, size=1000, species=(("B", 0.45),)),   # species_best (only B)
        trk(3, 3, 0.35, size=1000, species=(("A", 0.35),)),   # evictable, lowest conf
        trk(4, 4, 0.35, size=1000, species=(("B", 0.35),)),   # evictable, same conf, older
        trk(5, 5, 0.60, size=1000, species=(("A", 0.30),)),   # evictable, higher conf
        trk(6, 0, 0.30, size=1000, species=(("A", 0.30),), hours=2),   # live window
    ]
    got = by_id(retention.plan(tracks, NOW, pol))
    # total kept = 6000; need <= 2500 -> must evict until only protected (3 x 1000) remain...
    assert got[1].keep and got[2].keep and got[6].keep       # protected, even over budget
    assert [i for i in (4, 3, 5) if not got[i].keep] == [4, 3, 5]
    assert all(got[i].reason == "over_budget" for i in (3, 4, 5))


def test_budget_stops_as_soon_as_under_cap_and_order_is_conf_then_age():
    pol = Policy(best_per_species=0, max_bytes=2000, low_days=30)
    tracks = [
        trk(1, 10, 0.50, size=1000), trk(2, 20, 0.50, size=1000),   # equal conf: older goes first
        trk(3, 5, 0.40, size=1000), trk(4, 6, 0.60, size=1000),
    ]
    got = by_id(retention.plan(tracks, NOW, pol))
    assert {i for i, d in got.items() if not d.keep} == {3, 2}      # 0.40 then oldest 0.50
    assert got[1].keep and got[4].keep


def test_no_budget_eviction_when_cap_disabled_or_not_exceeded():
    tracks = [trk(i, 3, 0.4, size=1000) for i in range(5)]
    assert all(d.keep for d in retention.plan(tracks, NOW, Policy(best_per_species=0, max_bytes=0)))
    assert all(d.keep for d in retention.plan(tracks, NOW, Policy(best_per_species=0, max_bytes=5000)))


def test_plan_is_pure_does_not_mutate_inputs():
    tracks = [trk(1, 100, 0.95)]
    before = list(tracks)
    retention.plan(tracks, NOW, P)
    assert tracks == before


# ---- DB / filesystem shell -------------------------------------------------

def _seed(conn, tmp_path, name, started, conf, species="Wren", with_file=True, size=100):
    clip = tmp_path / f"{name}.mp3"
    if with_file:
        clip.write_bytes(b"x" * size)
    sid = storage.record_segment(
        conn, started_at=started, ended_at=started + timedelta(seconds=6), duration=6.0,
        detections=[identifier.Detection(species, "Sp", conf, 0.0, 3.0)],
        clip_paths={(0.0, 3.0): str(clip)},
    )
    return sid, clip


def test_run_dry_run_touches_nothing_and_apply_deletes(db_conn, tmp_path):
    keep_pol = Policy(best_per_species=0)
    _, hi = _seed(db_conn, tmp_path, "hi", NOW - timedelta(days=100), 0.95)
    _, lo = _seed(db_conn, tmp_path, "lo", NOW - timedelta(days=100), 0.40, species="Jay")
    _, new = _seed(db_conn, tmp_path, "new", NOW - timedelta(hours=1), 0.35, species="Owl")

    rep = retention.run(db_conn, keep_pol, now=NOW, dry_run=True)
    assert rep["summary"]["delete"]["low_conf"] == {"count": 1, "bytes": 100}
    assert rep["summary"]["keep"]["high_conf"]["count"] == 1
    assert rep["summary"]["keep"]["live_window"]["count"] == 1
    assert lo.exists()

    rep = retention.run(db_conn, keep_pol, now=NOW)
    assert (rep["deleted"], rep["bytes_freed"]) == (1, 100)
    assert hi.exists() and new.exists() and not lo.exists()
    paths = {r["id"]: r["clip_path"] for r in db_conn.execute("SELECT id, clip_path FROM tracks")}
    assert sum(p is None for p in paths.values()) == 1


def test_missing_files_are_reported_but_left_alone(db_conn, tmp_path):
    _seed(db_conn, tmp_path, "gone", NOW - timedelta(days=100), 0.40, with_file=False)
    rep = retention.run(db_conn, Policy(best_per_species=0), now=NOW)
    assert rep["missing_files"] == 1 and rep["considered"] == 0 and rep["deleted"] == 0
    assert db_conn.execute("SELECT clip_path FROM tracks").fetchone()["clip_path"] is not None


def test_assume_present_dry_run_skips_stat(db_conn, tmp_path):
    _seed(db_conn, tmp_path, "gone", NOW - timedelta(days=100), 0.95, with_file=False)
    rep = retention.run(db_conn, Policy(best_per_species=0), now=NOW, dry_run=True,
                        assume_present=True)
    assert rep["summary"]["keep"]["high_conf"]["count"] == 1
    assert rep["summary"]["keep"]["high_conf"]["bytes"] == retention.ASSUMED_CLIP_BYTES


def test_max_conf_written_on_insert_and_backfilled(db_conn):
    storage.record_segment(
        db_conn, started_at=NOW, ended_at=NOW + timedelta(seconds=6), duration=6.0,
        detections=[identifier.Detection("A", "a", 0.55, 0.0, 3.0),
                    identifier.Detection("B", "b", 0.81, 0.0, 3.0)],
    )
    assert db_conn.execute("SELECT max_conf FROM tracks").fetchone()[0] == 0.81
    db_conn.execute("UPDATE tracks SET max_conf = NULL")
    storage._migrate(db_conn)
    assert db_conn.execute("SELECT max_conf FROM tracks").fetchone()[0] == 0.81


def test_old_mechanisms_are_gone():
    for name in ("cleanup_dashboard_audio", "protected_dashboard_detection_ids",
                 "expire_track_clips", "_protected_dashboard_tracks",
                 "_protected_dashboard_segments"):
        assert not hasattr(storage, name)


def test_sample_clips_ignore_aged_out_audio(db_conn, tmp_path):
    """Dashboard clip samples must filter on a still-present path (step 6)."""
    import dashboard
    sid, clip = _seed(db_conn, tmp_path, "c", NOW - timedelta(days=100), 0.95)
    db_conn.execute("UPDATE tracks SET clip_path = NULL")
    db_conn.commit()
    out = dashboard._dash_sample_clips(db_conn, min_conf=0.9, per_species=3, excluded_days=())
    assert out == {} or all(not v for v in out.values())
