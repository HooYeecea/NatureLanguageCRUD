"""Bound request buffering, per-credential load, and actual model calls."""
import asyncio
import hashlib
import json
import time
from contextvars import ContextVar
from threading import Lock

from starlette.responses import JSONResponse
from app import config
from app.meta_db import meta_conn

class ResourceLimitExceeded(ValueError):
    pass


principal = ContextVar("resource_principal", default="local")


def consume_budget(key, limit, seconds):
    """Persistent fixed-window counter shared by processes using this metadata DB."""
    now = int(time.time())
    window = now // seconds
    with meta_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS resource_budgets (key TEXT, window INTEGER, count INTEGER, expires INTEGER, PRIMARY KEY(key, window))")
        conn.execute("DELETE FROM resource_budgets WHERE expires < ?", (now,))
        row = conn.execute("SELECT count FROM resource_budgets WHERE key=? AND window=?", (key, window)).fetchone()
        if row and row[0] >= limit:
            return False
        conn.execute("INSERT INTO resource_budgets VALUES(?,?,1,?) ON CONFLICT(key,window) DO UPDATE SET count=count+1",
                     (key, window, (window + 1) * seconds))
        return True


class ResourceLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self.active = {}
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        # Auth middleware has already resolved identity. Header spellings cannot split quotas.
        identity = scope.get("state", {}).get("is_admin")
        credential = ("admin" if identity is True else "operator" if identity is False else "public").encode()
        key = hashlib.sha256(credential).hexdigest()
        with self.lock:
            allowed = (self.active.get(key, 0) < config.MAX_CONCURRENT_REQUESTS
                       and sum(self.active.values()) < config.MAX_TOTAL_REQUESTS)
            if allowed:
                self.active[key] = self.active.get(key, 0) + 1
        if not allowed:
            return await JSONResponse({"detail": "Too many concurrent requests"}, 429)(scope, receive, send)
        token = principal.set(key)
        try:
            if not await asyncio.to_thread(consume_budget, "request:" + key, config.REQUESTS_PER_MINUTE, 60):
                return await JSONResponse({"detail": "Request quota exceeded"}, 429, headers={"Retry-After": "60"})(scope, receive, send)
            # Read completely before invoking handlers, including chunked requests.
            body = bytearray()
            deadline = time.monotonic() + 15
            while True:
                try:
                    message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
                except TimeoutError:
                    return await JSONResponse({"detail": "Request body timeout"}, 408)(scope, receive, send)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > config.MAX_REQUEST_BYTES:
                    return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            await self.app(scope, bounded_receive, send)
        finally:
            principal.reset(token)
            with self.lock:
                self.active[key] -= 1
                if not self.active[key]:
                    del self.active[key]


def limited_completion(client, **kwargs):
    # Count each generation/validation/rewrite; disable SDK retries at client creation.
    if len(json.dumps(kwargs.get("messages", []), ensure_ascii=False).encode()) > config.MAX_LLM_INPUT_BYTES:
        raise ResourceLimitExceeded("Model context exceeds byte budget")
    if not consume_budget("llm:" + principal.get(), config.LLM_CALLS_PER_HOUR, 3600):
        raise ResourceLimitExceeded("Model request quota exceeded")
    if not consume_budget("llm:global", config.LLM_TOTAL_CALLS_PER_HOUR, 3600):
        raise ResourceLimitExceeded("Global model request quota exceeded")
    kwargs["max_tokens"] = min(kwargs.get("max_tokens") or config.MAX_LLM_OUTPUT_TOKENS, config.MAX_LLM_OUTPUT_TOKENS)
    return client.chat.completions.create(**kwargs)
