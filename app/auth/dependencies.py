"""FastAPI auth dependencies.

``AuthMiddleware`` validates the access token and attaches an ``AuthPrincipal``
to ``request.state``; these helpers expose it to route handlers.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from auth.security import AuthPrincipal


async def get_current_user(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "user", None)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return principal


def require_role(*roles: str) -> Callable:
    async def checker(
        principal: AuthPrincipal = Depends(get_current_user),
    ) -> AuthPrincipal:
        if principal.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
            )
        return principal

    return checker
