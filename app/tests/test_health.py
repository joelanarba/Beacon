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
