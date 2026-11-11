"""Versioned API aliases and retired legacy routes."""

from __future__ import annotations

import pytest

import dashboard


def test_every_api_route_has_a_v1_alias():
    rules = {r.rule for r in dashboard.app.url_map.iter_rules()}
    api = {r for r in rules if r.startswith("/api/") and not r.startswith("/api/v1/")}
    assert api, "expected unversioned api routes"
    for r in api:
        assert "/api/v1/" + r[len("/api/"):] in rules, r


def test_register_v1_aliases_is_idempotent():
    assert dashboard.register_v1_aliases() == 0


@pytest.mark.parametrize(
    "name", ["summary", "sure", "health", "species/bewicks_wren"]
)
def test_dash_api_v1_matches_legacy_path(client, name, monkeypatch):
    monkeypatch.setattr(dashboard, "_CACHE", {})
    old = client.get(f"/api/dash/{name}")
    new = client.get(f"/api/v1/dash/{name}")
    assert old.status_code == new.status_code == 200
    if name in ("summary", "sure"):  # generated_at may tick between calls
        o, n = old.get_json(), new.get_json()
        o["meta"].pop("generated_at"), n["meta"].pop("generated_at")
        assert o == n
    elif name != "health":
        assert old.get_json() == new.get_json()


def test_recent_and_digest_v1(client, seeded_conn):
    assert client.get("/api/v1/recent").status_code == 200
    assert client.get("/api/v1/digest?day=2026-05-30").status_code == 200


def test_bird_redirects_to_spa_species_page(client):
    resp = client.get("/bird/bewicks_wren?day=2026-05-30")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dash/species/bewicks_wren")


@pytest.mark.parametrize("path", ["/live", "/realtime", "/live?hide_low=1"])
def test_live_pages_redirect_to_spa_live(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dash/live")


@pytest.mark.parametrize(
    "path",
    ["/dex", "/heard", "/timeline", "/trends", "/prevalence", "/data", "/twitter"],
)
def test_pages_without_spa_equivalent_stay(client, path):
    assert client.get(path).status_code == 200
