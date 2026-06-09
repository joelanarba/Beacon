"""Auth endpoints: login, refresh (with rotation), logout, and a protected
``/auth/me`` that exercises the middleware.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.security import (
    AuthPrincipal,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from models.db import RefreshToken, User, get_session
from models.schemas import LoginRequest, RefreshRequest, Token, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = status.HTTP_401_UNAUTHORIZED


@router.post("/login", response_model=Token)
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_session)
) -> Token:
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(body.password, user.hashed_password)
    ):
        raise HTTPException(status_code=_UNAUTHORIZED, detail="Invalid credentials")

    access = create_access_token(user.id, extra={"role": user.role.value})
    refresh, jti, expires_at = create_refresh_token(user.id)
    session.add(RefreshToken(jti=jti, user_id=user.id, expires_at=expires_at))
    await session.commit()
    return Token(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=Token)
async def refresh(
    body: RefreshRequest, session: AsyncSession = Depends(get_session)
) -> Token:
    try:
        claims = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=_UNAUTHORIZED, detail="Invalid refresh token"
        ) from None
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=_UNAUTHORIZED, detail="Invalid refresh token")

    result = await session.execute(
        select(RefreshToken).where(RefreshToken.jti == claims.get("jti"))
    )
    stored = result.scalar_one_or_none()
    if stored is None or stored.revoked:
        raise HTTPException(
            status_code=_UNAUTHORIZED, detail="Refresh token revoked or unknown"
        )

    # Rotate: revoke the presented token, then issue a fresh pair.
    stored.revoked = True
    user_result = await session.execute(
        select(User).where(User.id == claims.get("sub"))
    )
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        await session.commit()
        raise HTTPException(status_code=_UNAUTHORIZED, detail="User inactive")

    access = create_access_token(user.id, extra={"role": user.role.value})
    new_refresh, new_jti, expires_at = create_refresh_token(user.id)
    session.add(RefreshToken(jti=new_jti, user_id=user.id, expires_at=expires_at))
    await session.commit()
    return Token(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        claims = decode_token(body.refresh_token)
    except JWTError:
        return  # idempotent: an unparseable token is already "logged out"

    result = await session.execute(
        select(RefreshToken).where(RefreshToken.jti == claims.get("jti"))
    )
    stored = result.scalar_one_or_none()
    if stored is not None and not stored.revoked:
        stored.revoked = True
        await session.commit()


@router.get("/me", response_model=UserRead)
async def me(
    principal: AuthPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    result = await session.execute(select(User).where(User.id == principal.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=_UNAUTHORIZED, detail="User not found")
    return user
