"""AuthMiddleware — stateless access-token gate.

Every request must carry a valid ``Bearer`` *access* token, except the configured
open allowlist: health, auth endpoints, and the ingestion channels (open by
design, since a reporter on USSD/SMS cannot authenticate). Development-only
surfaces such as docs, metrics, and the simulator are opened only when their
settings allow it. On success the decoded identity is attached to
``request.state.user`` for downstream handlers.
"""

from __future__ import annotations

from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.security import AuthPrincipal, decode_token
from config import get_settings

_ALWAYS_OPEN_PREFIXES = (
    "/health",
    "/auth/login",
    "/auth/refresh",
    "/auth/logout",
    "/ingest",
)


def _is_open(path: str) -> bool:
    settings = get_settings()
    prefixes = list(_ALWAYS_OPEN_PREFIXES)
    if settings.docs_enabled:
        prefixes.extend(("/docs", "/redoc", "/openapi.json"))
    if settings.metrics_public:
        prefixes.append("/metrics")
    if settings.simulator_enabled:
        prefixes.append("/sim")
    return any(path == p or path.startswith(p + "/") for p in prefixes)


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
