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


def test_data_view(client):
    resp = client.get("/data?day=2026-05-30")
    assert resp.status_code == 200
    assert b"Bewick" in resp.data or b"2" in resp.data
