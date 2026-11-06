"""Security headers and a small in-process rate limit for the HTTP API."""

import os
import time
from collections.abc import MutableMapping

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

_DEFAULT_READ_LIMIT = 240
_DEFAULT_RUN_LIMIT = 6
_WINDOW_SECONDS = 60
_MAX_BODY_BYTES = 1_048_576

_DOCS_PREFIXES = ("/docs", "/redoc")

_STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'"
)
# Swagger and ReDoc load their UI from the jsDelivr CDN and use inline scripts.
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class SlidingWindowLimiter:
    """Fixed-window counter keyed by client address and bucket."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: float, now: float) -> bool:
        hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < window_seconds]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 10000:
            self._hits = {item: stamps for item, stamps in self._hits.items() if stamps}
        return True


def apply_security_headers(headers: MutableMapping[str, str], scope: dict) -> None:
    """Set response headers that do not depend on the handler."""

    path = _request_path(scope)
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    )
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    headers["X-Permitted-Cross-Domain-Policies"] = "none"
    headers["Content-Security-Policy"] = _csp(path)
    headers["Cache-Control"] = "no-store"
    if scope.get("scheme") == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


class SecurityMiddleware:
    """Rate limit public routes and attach security headers on the way out."""

    def __init__(self, app, limiter: SlidingWindowLimiter | None = None) -> None:
        self.app = app
        self.limiter = limiter or SlidingWindowLimiter()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = _content_length(scope)
        if length is not None and (length < 0 or length > _MAX_BODY_BYTES):
            response = JSONResponse(
                status_code=413,
                content={"detail": "Request body is too large"},
            )
            apply_security_headers(response.headers, scope)
            await response(scope, receive, send)
            return

        path = _request_path(scope)
        method = scope.get("method", "GET")
        client = (scope.get("client") or ("unknown", 0))[0]
        bucket = "run" if method == "POST" and path == "/quality/run" else "read"
        limit = _run_limit() if bucket == "run" else _read_limit()
        if not self.limiter.allow(f"{client}:{bucket}", limit, _WINDOW_SECONDS, time.monotonic()):
            response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
            apply_security_headers(response.headers, scope)
            await response(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                apply_security_headers(MutableHeaders(raw=message["headers"]), scope)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _request_path(scope: dict) -> str:
    path = scope.get("path") or "/"
    if len(path) > 1:
        return path.rstrip("/")
    return path


def _csp(path: str) -> str:
    if path.startswith(_DOCS_PREFIXES):
        return _DOCS_CSP
    return _STRICT_CSP


def _content_length(scope: dict) -> int | None:
    for name, value in scope.get("headers") or []:
        if name.lower() != b"content-length":
            continue
        try:
            return int(value)
        except ValueError:
            return -1
    return None


def _read_limit() -> int:
    return _env_limit("DQ_RATE_LIMIT", _DEFAULT_READ_LIMIT)


def _run_limit() -> int:
    return _env_limit("DQ_RUN_RATE_LIMIT", _DEFAULT_RUN_LIMIT)


def _env_limit(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 1:
        return default
    return value
