"""Liveness + metrics endpoints (no DB required)."""

from __future__ import annotations


async def test_health_ok(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_metrics_exposition(app_client):
    resp = await app_client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert b"beacon_app_info" in resp.content


async def test_readiness_is_not_liveness_without_event_worker(app_client):
    resp = await app_client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready"}


async def test_logout_invalid_token_has_no_response_body(app_client):
    resp = await app_client.post("/auth/logout", json={"refresh_token": "invalid"})
    assert resp.status_code == 204
    assert resp.content == b""
