"""Integration: login -> /auth/me -> refresh rotation -> logout (uses the DB)."""

from __future__ import annotations

from auth.security import hash_password
from models.db import User, UserRole


async def _make_user(
    session, email: str = "dispatcher@test.local", password: str = "pw12345"
) -> tuple[str, str]:
    session.add(
        User(
            email=email,
            full_name="Test Dispatcher",
            hashed_password=hash_password(password),
            role=UserRole.DISPATCHER,
        )
    )
    await session.commit()
    return email, password


async def test_login_me_refresh_logout(client, db_session):
    email, password = await _make_user(db_session)

    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["token_type"] == "bearer"

    # Protected route is rejected without a token...
    assert (await client.get("/auth/me")).status_code == 401

    # ...and accepted with one.
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == email

    # Refresh issues a new pair and revokes the presented refresh token.
    rotated = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_tokens = rotated.json()

    reused = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    # Logout revokes the current refresh token.
    out = await client.post(
        "/auth/logout", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert out.status_code == 204
    after = await client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after.status_code == 401


async def test_login_bad_credentials(client, db_session):
    await _make_user(db_session, email="x@test.local", password="rightpw")
    resp = await client.post(
        "/auth/login", json={"email": "x@test.local", "password": "wrongpw"}
    )
    assert resp.status_code == 401
