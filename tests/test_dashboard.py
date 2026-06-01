"""Tests for dashboard helpers and routes."""

from __future__ import annotations

from datetime import date

import dashboard


def test_parse_day_arg_defaults_to_today():
    show_all, day = dashboard._parse_day_arg(None)
    assert show_all is False
    assert day == date.today().isoformat()


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


def test_index_gallery_mode(client):
    resp = client.get("/?day=2026-05-30&mode=gallery")
    assert resp.status_code == 200


def test_bird_detail(client):
    resp = client.get("/bird/bewicks_wren?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Bewick" in resp.data


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
