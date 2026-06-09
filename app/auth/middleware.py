"""AuthMiddleware — stateless access-token gate.

Every request must carry a valid ``Bearer`` *access* token, except the open
allowlist: health, metrics, API docs, the login/refresh/logout endpoints, and
the ingestion channels (open by design, since a reporter on USSD/SMS cannot
authenticate). On success the decoded identity is attached to
``request.state.user`` for downstream handlers.
"""

from __future__ import annotations

from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.security import AuthPrincipal, decode_token

_OPEN_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/login",
    "/auth/refresh",
    "/auth/logout",
    "/ingest",
    "/sim",
)


def _is_open(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _OPEN_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or _is_open(request.url.path):
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        token = header[len("Bearer ") :]
        try:
            claims = decode_token(token)
        except JWTError:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
        if claims.get("type") != "access":
            return JSONResponse({"detail": "Invalid token type"}, status_code=401)

        request.state.user = AuthPrincipal(
            user_id=claims.get("sub", ""), role=claims.get("role", "")
        )
        return await call_next(request)
