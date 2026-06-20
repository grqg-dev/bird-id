"""Tests for dashboard helpers and routes."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import dashboard
import identifier
import storage


def test_audio_mimetype():
    assert dashboard._audio_mimetype("/a/clip.mp3") == "audio/mpeg"
    assert dashboard._audio_mimetype("/a/clip.wav") == "audio/wav"


def test_audio_serves_track_clip_without_segment_wav(client, seeded_conn, tmp_path):
    import storage

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF" + b"\x00" * 100)
    track = storage.get_track(seeded_conn, 1, 0.0, 3.0)
    storage.set_track_clip_path(seeded_conn, track["id"], str(clip))
    seeded_conn.execute("UPDATE segments SET wav_path = NULL WHERE id = 1")
    seeded_conn.commit()

    resp = client.get("/audio/1?start=0.0&end=3.0")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/wav"


def test_spec_cache_path_windowed(tmp_path):
    base = tmp_path / "recordings"
    p = dashboard._spec_cache_path(42, 0.0, 3.0, base)
    assert p == base / "cache" / "spectrograms" / "spec_42_0_3000.png"


def test_spec_cache_path_full_segment(tmp_path):
    base = tmp_path / "recordings"
    p = dashboard._spec_cache_path(7, None, None, base)
    assert p == base / "cache" / "spectrograms" / "spec_7_full.png"


def test_callviz_cache_path_windowed(tmp_path):
    base = tmp_path / "recordings"
    p = dashboard._callviz_cache_path(42, 0.0, 3.0, base)
    assert p == base / "cache" / "callviz" / "viz_42_0_3000.json"


def test_call_href_preserves_day_and_sort():
    href = dashboard._call_href(
        1,
        0.0,
        3.0,
        "bewicks_wren",
        show_all=False,
        selected_day="2026-05-30",
        sort="peak",
        hide_low=True,
    )
    assert href == "/call/1?start=0.0&end=3.0&slug=bewicks_wren&day=2026-05-30&sort=peak&hide_low=1"


def test_parse_day_arg_defaults_to_all_time():
    show_all, day = dashboard._parse_day_arg(None)
    assert show_all is True
    assert day == date.today().isoformat()


def test_index_defaults_all_time_discovered(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Species discovered (all time)" in resp.data
    assert b"Discovered" in resp.data


def test_parse_day_arg_all():
    show_all, day = dashboard._parse_day_arg("all")
    assert show_all is True


def test_parse_day_arg_specific_day():
    show_all, day = dashboard._parse_day_arg("2026-05-30")
    assert show_all is False
    assert day == "2026-05-30"


def test_format_span_seconds():
    assert dashboard._format_span("2026-05-30T08:00:00", "2026-05-30T08:00:30") == "30s"


def test_is_tentative():
    assert dashboard._is_tentative(0.5, 1) is True
    assert dashboard._is_tentative(0.8, 3) is False


def test_filter_dex_rows_hide_low():
    rows = [
        {"common_name": "Strong", "peak_conf": 0.85},
        {"common_name": "Weak", "peak_conf": 0.55},
        {"common_name": "Edge", "peak_conf": 0.7},
    ]
    kept = dashboard._filter_dex_rows(rows, hide_low=True)
    assert [r["common_name"] for r in kept] == ["Strong", "Edge"]
    assert len(dashboard._filter_dex_rows(rows, hide_low=False)) == 3


def test_index_hide_low_toggle(client):
    resp = client.get("/?day=2026-05-30&hide_low=1")
    assert resp.status_code == 200
    assert b"Peak" in resp.data
    assert b"Oak Titmouse" in resp.data
    assert b"Bewick" in resp.data


def test_index_renders_species(client):
    resp = client.get("/?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Bewick" in resp.data
    assert b"Oak Titmouse" in resp.data


def test_index_renders_viz_btn(client):
    resp = client.get("/?day=2026-05-30")
    assert resp.status_code == 200
    assert b"viz-btn" in resp.data
    assert b"/call/1?" in resp.data
    assert b"slug=bewicks_wren" in resp.data


def test_call_view(client):
    resp = client.get(
        "/call/1?start=0.0&end=3.0&slug=bewicks_wren&day=2026-05-30"
    )
    assert resp.status_code == 200
    assert b"Call Chamber" in resp.data
    assert b"chamber-canvas" in resp.data
    assert b"initSpectrogram" in resp.data
    assert b"time-readout" in resp.data
    assert b"t-btn" in resp.data
    assert b"stage-load" in resp.data
    assert b"s clip" in resp.data
    assert b"hero-card" in resp.data
    assert b"hero-nav" in resp.data
    assert b"nav-group call" in resp.data
    assert b"nav-group bird" in resp.data
    assert b"1 / 1" in resp.data
    assert b"/static/three.min.js" not in resp.data
    assert b"Bewick" in resp.data
    assert b"Oak Titmouse" in resp.data  # next bird nav


def test_species_call_order_and_clip_nav(seeded_conn):
    calls = dashboard._species_call_order(
        seeded_conn,
        "Bewick's Wren",
        show_all=False,
        selected_day="2026-05-30",
    )
    assert len(calls) == 1
    assert calls[0]["peak_conf"] == 0.92

    day2 = datetime(2026, 5, 30, 9, 0, 0)
    storage.record_segment(
        seeded_conn,
        started_at=day2,
        ended_at=day2 + timedelta(seconds=6),
        duration=6.0,
        detections=[
            identifier.Detection("Bewick's Wren", "Thryomanes bewickii", 0.81, 0.0, 3.0),
        ],
        wav_path="/tmp/seg2.wav",
    )
    calls = dashboard._species_call_order(
        seeded_conn,
        "Bewick's Wren",
        show_all=False,
        selected_day="2026-05-30",
    )
    assert len(calls) == 2
    assert calls[0]["peak_conf"] == 0.92
    assert calls[1]["peak_conf"] == 0.81


def test_call_view_not_in_dex(client):
    resp = client.get("/call/1?start=9.0&end=12.0&slug=not_a_bird&day=2026-05-30")
    assert resp.status_code == 404


def test_callviz_json_missing_audio(client):
    resp = client.get("/callviz/1.json?start=0.0&end=3.0")
    assert resp.status_code == 404


def test_index_gallery_mode(client):
    resp = client.get("/?day=2026-05-30&mode=gallery")
    assert resp.status_code == 200


def test_bird_detail(client):
    resp = client.get("/bird/bewicks_wren?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Bewick" in resp.data
    assert b"nav-group call" in resp.data
    assert b"nav-group bird" in resp.data


def test_bird_detail_unknown_slug(client):
    resp = client.get("/bird/not_a_real_bird?day=2026-05-30")
    assert resp.status_code == 404


def test_bird_detail_back_link_is_absolute(client):
    resp = client.get("/bird/bewicks_wren?day=2026-05-30&mode=gallery")
    assert resp.status_code == 200
    assert b'href="/?day=2026-05-30&amp;mode=gallery"' in resp.data
    assert b'href="?mode=gallery' not in resp.data
    assert b'href="?day=2026-05-30' not in resp.data


def test_data_view_back_link_is_absolute(client):
    resp = client.get("/data?day=2026-05-30")
    assert resp.status_code == 200
    assert b'href="/?day=2026-05-30"' in resp.data
    assert b'href="?day=2026-05-30"' not in resp.data


def test_data_view(client):
    resp = client.get("/data?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Data report" in resp.data or b"report" in resp.data.lower()


def test_timeline_day_view(client):
    resp = client.get("/timeline?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Timeline" in resp.data
    assert b"Bewick" in resp.data
    assert b"Oak Titmouse" in resp.data


def test_timeline_all_time(client):
    resp = client.get("/timeline?day=all")
    assert resp.status_code == 200
    assert b"05-30" in resp.data


def test_timeline_back_link_is_absolute(client):
    resp = client.get("/timeline?day=2026-05-30")
    assert resp.status_code == 200
    assert b'href="/?day=2026-05-30"' in resp.data


def test_timeline_empty_day(client):
    resp = client.get("/timeline?day=2026-01-01")
    assert resp.status_code == 200
    assert b"No detections" in resp.data


def _seed_recent_detection(conn, *, minutes_ago: float = 5, name: str = "Live Bird", conf: float = 0.88):
    from datetime import datetime, timedelta

    import identifier
    import storage

    now = datetime.now()
    started = now - timedelta(minutes=minutes_ago)
    storage.record_segment(
        conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection(name, "Live sp", conf, 0.0, 3.0),
        ],
        wav_path="/tmp/live_seg.wav",
    )


def test_live_feed_clip_only(client, seeded_conn, tmp_path):
    from datetime import datetime, timedelta

    import identifier
    import storage

    clip = tmp_path / "live.mp3"
    clip.write_bytes(b"mp3")
    now = datetime.now()
    started = now - timedelta(minutes=5)
    storage.record_segment(
        seeded_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Clip Feed Bird", "Sp", 0.88, 0.0, 3.0),
        ],
        wav_path=None,
        clip_paths={(0.0, 3.0): str(clip)},
    )
    resp = client.get("/live")
    assert resp.status_code == 200
    assert b"Clip Feed Bird" in resp.data


def test_live_feed_page(client, seeded_conn):
    _seed_recent_detection(seeded_conn, name="Feed Test Bird")
    resp = client.get("/live")
    assert resp.status_code == 200
    assert b"Live feed" in resp.data
    assert b"Feed Test Bird" in resp.data
    assert b"Last 24h" in resp.data
    assert b"/spectrogram/" in resp.data
    assert b"Conf" in resp.data


def test_live_feed_hide_low(client, seeded_conn):
    _seed_recent_detection(seeded_conn, name="Low Conf Bird", conf=0.5)
    _seed_recent_detection(seeded_conn, minutes_ago=5, name="High Conf Bird", conf=0.85)
    resp = client.get("/live?hide_low=1")
    assert resp.status_code == 200
    assert b"High Conf Bird" in resp.data
    assert b"Low Conf Bird" not in resp.data


def test_api_recent_hide_low(client, seeded_conn):
    _seed_recent_detection(seeded_conn, name="Low Api", conf=0.4)
    _seed_recent_detection(seeded_conn, minutes_ago=5, name="High Api", conf=0.9)
    resp = client.get("/api/recent?hide_low=1")
    assert resp.status_code == 200
    names = [e["common_name"] for e in resp.get_json()["events"]]
    assert "High Api" in names
    assert "Low Api" not in names


def test_api_recent_returns_events(client, seeded_conn):
    _seed_recent_detection(seeded_conn, minutes_ago=10, name="Api Bird")
    resp = client.get("/api/recent")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["events"]) >= 1
    assert data["events"][0]["common_name"] == "Api Bird"
    assert data["latest"]


def test_api_recent_since_filters(client, seeded_conn):
    from datetime import datetime

    import storage

    _seed_recent_detection(seeded_conn, minutes_ago=30, name="Older Api")
    _seed_recent_detection(seeded_conn, minutes_ago=2, name="Newer Api")
    rows = storage.recent_feed(seeded_conn, now=datetime.now())
    newer = next(r for r in rows if r["common_name"] == "Newer Api")
    since_resp = client.get(f"/api/recent?since={newer['heard_at']}")
    since_names = [e["common_name"] for e in since_resp.get_json()["events"]]
    assert "Newer Api" not in since_names


def test_format_heard_clock():
    assert dashboard._format_heard_clock("2026-06-01T14:32:05") == "2:32 PM"
    assert dashboard._format_heard_clock("2026-06-01T08:05:00") == "8:05 AM"


def test_realtime_page_loads(client):
    resp = client.get("/realtime")
    assert resp.status_code == 200
    assert b"Real time" in resp.data
    assert b"Listening" in resp.data


def test_realtime_page_shows_recent_birds(client, seeded_conn):
    _seed_recent_detection(seeded_conn, name="Realtime Bird", minutes_ago=5)
    resp = client.get("/realtime")
    assert resp.status_code == 200
    assert b"Realtime Bird" in resp.data


def test_realtime_page_has_nav_links(client):
    resp = client.get("/realtime")
    assert resp.status_code == 200
    assert b'href="/live"' in resp.data
    assert b'href="/"' in resp.data


def test_twitter_view_renders(client):
    resp = client.get("/twitter")
    assert resp.status_code == 200
    assert b"Bird feed" in resp.data
    assert b"/bird/" in resp.data
    assert b"spectrogram" in resp.data
    assert b"data-theme" in resp.data


def test_twitter_chronological_sort(client):
    resp = client.get("/twitter?sort=time")
    assert resp.status_code == 200
    assert b"Latest" in resp.data


def test_twitter_confidence_sort(client):
    resp = client.get("/twitter?sort=conf")
    assert resp.status_code == 200
    assert b"Top conf" in resp.data


def test_twitter_api_returns_json(client):
    resp = client.get("/api/twitter?page=1")
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert "events" in data
    assert "has_more" in data


def test_twitter_api_respects_pagination(client):
    resp = client.get("/api/twitter?page=1")
    data = resp.get_json()
    resp2 = client.get("/api/twitter?page=2")
    data2 = resp2.get_json()
    assert data2["events"] == [] or len(data2["events"]) < 50


def test_twitter_nav_link_present_on_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"/twitter" in resp.data
