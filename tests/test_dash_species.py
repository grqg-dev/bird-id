"""Species page API, clip retention explanations, and SPA path hosting."""

from __future__ import annotations

from datetime import datetime, timedelta

import identifier
import retention
import storage

NOW = datetime(2026, 11, 11, 12, 0, 0)
POLICY = retention.Policy()


def test_keep_info_tiers():
    old = NOW - timedelta(days=100)
    assert retention.keep_info(0.5, NOW - timedelta(hours=2), 99, NOW, POLICY)[0] == "live_window"
    assert retention.keep_info(0.95, old, 0, NOW, POLICY) == ("species_best", None)
    reason, until = retention.keep_info(0.95, old, 10, NOW, POLICY)
    assert reason == "high_conf" and until == old + timedelta(days=365)
    assert retention.keep_info(0.75, old, 10, NOW, POLICY)[0] == "mid_conf"
    assert retention.keep_info(0.4, old, 10, NOW, POLICY)[1] == old + timedelta(days=7)


def _seed_clips(conn, tmp_path, n=12):
    clip = tmp_path / "c.wav"
    clip.write_bytes(b"RIFF" + b"\x00" * 100)
    for i in range(n):
        when = datetime(2026, 7, 1, 7, 0, 0) + timedelta(hours=i)
        d = identifier.Detection("Sure Bird", "Certus avis", 0.99 - i * 0.02, 0.0, 3.0)
        storage.record_segment(
            conn, started_at=when, ended_at=when + timedelta(seconds=3), duration=3.0,
            detections=[d], wav_path=None, clip_paths={(0.0, 3.0): str(clip)},
        )


def test_species_api(client, seeded_conn, tmp_path, monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    _seed_clips(seeded_conn, tmp_path)
    data = client.get("/api/dash/species/sure_bird").get_json()
    assert data["name"] == "Sure Bird" and data["total"] == 12 and data["days"] == 1
    assert len(data["hourly"]) == 24 and sum(data["hourly"]) == 12
    clips = data["clips"]
    assert len(clips) == 12 and clips[0]["conf"] >= clips[1]["conf"]
    # best 10 are kept indefinitely, #11 and #12 follow the confidence tier (0.77 → mid tier, 30 d)
    assert clips[0]["keep"] == {"reason": "species_best", "until": None}
    assert clips[11]["keep"]["reason"] == "mid_conf"  # conf 0.77
    assert clips[11]["keep"]["until"] is not None


def test_species_api_404(client):
    assert client.get("/api/dash/species/not_a_bird").status_code == 404


def test_sure_and_summary_clips_carry_keep(client, seeded_conn, tmp_path, monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "_CACHE", {})
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    _seed_clips(seeded_conn, tmp_path, n=3)
    sure = client.get("/api/dash/sure").get_json()
    clip = next(s for s in sure["species"] if s["name"] == "Sure Bird")["clips"][0]
    assert clip["keep"]["reason"] == "species_best"
    summ = client.get("/api/dash/summary").get_json()
    sp = next(s for s in summ["species"] if s["name"] == "Sure Bird")
    assert sp["clip"]["keep"]["until"] is None


def test_spa_paths_serve_index(client, tmp_path, monkeypatch):
    import dashboard

    (tmp_path / "index.html").write_text("<main>spa</main>")
    monkeypatch.setattr(dashboard, "_DASH_DIST", tmp_path)
    for path in ("/dash/species/house_finch", "/dash/day/2026-06-19", "/dash/live", "/dash/sure"):
        resp = client.get(path)
        assert resp.status_code == 200 and b"spa" in resp.data, path
    assert client.get("/dash/assets/missing.js").status_code == 404
