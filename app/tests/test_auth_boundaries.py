"""Token validation, dispatcher access, and refresh rotation concurrency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt
from sqlalchemy import select, text
from starlette.websockets import WebSocketDisconnect

from auth.router import refresh
from auth.security import create_access_token, create_refresh_token, decode_token
from config import get_settings
from main import app
from models.db import RefreshToken, User
from models.schemas import RefreshRequest


def signed_claims(**changes):
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "type": "access",
        "role": "DISPATCHER",
    }
    claims.update(changes)
    return claims


def encode(claims):
    settings = get_settings()
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


@pytest.mark.parametrize("claim", ["sub", "jti", "iat", "exp", "type"])
def test_token_requires_issued_identity_and_expiry(claim):
    claims = signed_claims()
    claims.pop(claim)
    with pytest.raises(JWTError):
        decode_token(encode(claims))


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": ""},
        {"jti": "not-a-uuid"},
        {"exp": None},
        {"iat": None},
        {"exp": float("inf")},
        {"type": ["access"]},
    ],
)
def test_malformed_claims_raise_authentication_error(changes):
    with pytest.raises(JWTError):
        decode_token(encode(signed_claims(**changes)))


@pytest.mark.parametrize("role", [None, "RESPONDER", "ADMINISTRATOR", ""])
def test_dispatch_socket_rejects_non_dispatcher_roles(role):
    token = create_access_token("synthetic-user", extra={"role": role})
    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(app).websocket_connect(f"/ws/dispatch?token={token}"):
            pass
    assert error.value.code == 4403


@pytest.mark.parametrize("role", ["DISPATCHER", "ADMIN"])
def test_dispatch_socket_accepts_dispatcher_roles(role):
    token = create_access_token("synthetic-user", extra={"role": role})
    with TestClient(app).websocket_connect(f"/ws/dispatch?token={token}"):
        pass


async def test_http_and_socket_reject_token_without_expiry(app_client):
    claims = signed_claims()
    claims.pop("exp")
    token = encode(claims)
    response = await app_client.get(
        "/protected-probe", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(app).websocket_connect(f"/ws/dispatch?token={token}"):
            pass
    assert error.value.code == 4401


async def test_refresh_is_single_use_under_concurrent_requests(session_factory):
    async with session_factory() as session:
        user = User(email="rotation@example.com", hashed_password="unused")
        session.add(user)
        await session.flush()
        token, jti, expires_at = create_refresh_token(user.id)
        session.add(RefreshToken(jti=jti, user_id=user.id, expires_at=expires_at))
        await session.commit()

    pids = []

    async def rotate():
        async with session_factory() as session:
            pids.append(await session.scalar(text("SELECT pg_backend_pid()")))
            try:
                return await refresh(RefreshRequest(refresh_token=token), session)
            except HTTPException as error:
                return error.status_code

    async with session_factory() as blocker:
        await blocker.execute(
            select(RefreshToken).where(RefreshToken.jti == jti).with_for_update()
        )
        tasks = [asyncio.create_task(rotate()) for _ in range(2)]
        try:
            # Both old UPDATE paths and corrected SELECT FOR UPDATE paths block
            # here. Release only after both requests are inside their transaction.
            async with asyncio.timeout(10):
                while len(pids) < 2:
                    await asyncio.sleep(0.01)
                while (
                    await blocker.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE pid IN (:first, :second) AND wait_event_type='Lock'"
                        ),
                        {"first": pids[0], "second": pids[1]},
                    )
                    < 2
                ):
                    await asyncio.sleep(0.01)
        finally:
            await blocker.rollback()
            results = await asyncio.gather(*tasks)
    assert sum(result == 401 for result in results) == 1
    assert sum(hasattr(result, "refresh_token") for result in results) == 1
    async with session_factory() as session:
        rows = (await session.scalars(select(RefreshToken))).all()
        assert len(rows) == 2
        assert sum(not row.revoked for row in rows) == 1


@pytest.mark.parametrize("invalid", ["expired", "wrong_subject"])
async def test_refresh_honors_stored_expiry_and_owner(client, db_session, invalid):
    owner = User(email="owner@example.com", hashed_password="unused")
    other = User(email="other@example.com", hashed_password="unused")
    db_session.add_all([owner, other])
    await db_session.flush()
    token, jti, expires_at = create_refresh_token(owner.id)
    if invalid == "expired":
        expires_at = datetime.now(UTC) - timedelta(seconds=5)
    else:
        claims = decode_token(token)
        claims["sub"] = other.id
        token = encode(claims)
    db_session.add(RefreshToken(jti=jti, user_id=owner.id, expires_at=expires_at))
    await db_session.commit()
    response = await client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401
