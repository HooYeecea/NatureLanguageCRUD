"""Optional shared API key gate for the workbench HTTP API."""

from __future__ import annotations

import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import WORKBENCH_API_KEY, assert_api_key_configured, api_key_required

# Paths that stay reachable without a key (so the UI can detect auth + health).
_OPEN_EXACT = frozenset(
    {
        "/health",
        "/api/auth/status",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
    }
)


def extract_api_key(request: Request) -> str:
    header = (request.headers.get("X-API-Key") or "").strip()
    if header:
        return header
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def keys_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    if len(provided) != len(expected):
        # Constant-time-ish reject without raising on length mismatch.
        secrets.compare_digest(provided, provided)
        return False
    return secrets.compare_digest(provided, expected)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Let CORS preflight through; browser will not attach X-API-Key on OPTIONS.
        if request.method == "OPTIONS":
            return await call_next(request)
        if not api_key_required():
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        # Keep trailing-slash variants of open paths open.
        candidates = {path, path + "/", request.url.path}
        if candidates & _OPEN_EXACT or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        provided = extract_api_key(request)
        if not keys_match(provided, WORKBENCH_API_KEY):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Invalid or missing API key. "
                    "Send X-API-Key (or Authorization: Bearer) matching WORKBENCH_API_KEY."
                },
            )
        return await call_next(request)


def ensure_auth_ready() -> None:
    assert_api_key_configured()
