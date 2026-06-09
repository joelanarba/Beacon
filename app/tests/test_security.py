"""Unit tests for auth primitives (no DB, no app)."""

from __future__ import annotations

import pytest
from jose import JWTError

from auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip():
    token = create_access_token("user-123", extra={"role": "DISPATCHER"})
    claims = decode_token(token)
    assert claims["sub"] == "user-123"
    assert claims["type"] == "access"
    assert claims["role"] == "DISPATCHER"


def test_refresh_token_carries_jti():
    token, jti, _expires_at = create_refresh_token("user-123")
    claims = decode_token(token)
    assert claims["type"] == "refresh"
    assert claims["jti"] == jti


def test_tampered_token_rejected():
    token = create_access_token("user-123")
    with pytest.raises(JWTError):
        decode_token(token + "tampered")
