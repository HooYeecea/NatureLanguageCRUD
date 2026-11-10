"""Optional shared API key gate for the workbench HTTP API."""

from __future__ import annotations

import secrets
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import WORKBENCH_API_KEY, assert_api_key_configured, api_key_required
from app import config

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
        secrets.compare_digest(provided.encode(), provided.encode())
        return False
    return secrets.compare_digest(provided.encode(), expected.encode())


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Let CORS preflight through; browser will not attach X-API-Key on OPTIONS.
        if request.method == "OPTIONS":
            return await call_next(request)
        if not api_key_required():
            if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                return JSONResponse(status_code=403, content={"detail": "Unauthenticated development access is loopback-only"})
            request.state.is_admin = True
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        # Keep trailing-slash variants of open paths open.
        candidates = {path, path + "/", request.url.path}
        if config.is_production() and path in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        if candidates & _OPEN_EXACT:
            return await call_next(request)

        provided = extract_api_key(request)
        admin = keys_match(provided, config.WORKBENCH_ADMIN_API_KEY)
        if not admin and not keys_match(provided, WORKBENCH_API_KEY):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Invalid or missing API key. "
                    "Send X-API-Key (or Authorization: Bearer) matching WORKBENCH_API_KEY."
                },
            )
        request.state.is_admin = admin
        if not admin:
            # Deny unknown endpoints by default. Operators can only query/mutate
            # explicitly assigned connections, never change their access policy.
            allowed = path in {"/api/connections", "/api/settings"} and request.method == "GET"
            match = re.fullmatch(r"/api/connections/([^/]+)(?:/(.*))?", path)
            if match and match[1] in config.WORKBENCH_CONNECTION_IDS:
                suffix = match[2] or ""
                allowed |= request.method == "GET" and suffix in {"", "policy"}
                allowed |= request.method == "POST" and suffix.startswith("query/")
                allowed |= request.method == "POST" and suffix.startswith("mutate/") and config.WORKBENCH_API_ROLE == "writer"
            if not allowed:
                return JSONResponse(status_code=403, content={"detail": "Administrator credential or connection assignment required"})
        return await call_next(request)


def ensure_auth_ready() -> None:
    assert_api_key_configured()
