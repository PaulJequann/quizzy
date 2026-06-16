from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import get_settings
from app.mcp.auth import StaticBearerTokenVerifier
from app.mcp.server import build_server


class HostedMcpRequestGuard(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: set[str],
        max_request_bytes: int,
    ) -> None:
        super().__init__(app)
        self.allowed_origins = allowed_origins
        self.max_request_bytes = max_request_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        if self._origin_is_blocked(origin):
            return JSONResponse({"error": "Origin is not allowed."}, status_code=403)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_bytes = int(content_length)
            except ValueError:
                return JSONResponse({"error": "Invalid Content-Length header."}, status_code=400)
            if request_bytes > self.max_request_bytes:
                return JSONResponse({"error": "MCP request body is too large."}, status_code=413)

        return await call_next(request)

    def _origin_is_blocked(self, origin: str | None) -> bool:
        return bool(origin and self.allowed_origins and origin not in self.allowed_origins)


def create_app():
    settings = get_settings()
    auth = _bearer_auth(settings.mcp_bearer_token)
    server = build_server(auth=auth)

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "quizzy-mcp"})

    return server.http_app(
        path="/mcp",
        stateless_http=True,
        middleware=[
            Middleware(
                HostedMcpRequestGuard,
                allowed_origins=_allowed_origins(settings.mcp_allowed_origins),
                max_request_bytes=settings.mcp_max_request_bytes,
            )
        ],
    )


def _bearer_auth(token: str) -> StaticBearerTokenVerifier:
    expected = token.strip()
    if not expected:
        raise RuntimeError("MCP_BEARER_TOKEN is required for hosted MCP HTTP.")
    return StaticBearerTokenVerifier(
        validate=lambda received: received == expected,
        required_scopes=["quizzy:mcp"],
        scopes=["quizzy:mcp"],
    )


def _allowed_origins(raw_origins: str) -> set[str]:
    return {origin.strip() for origin in raw_origins.split(",") if origin.strip()}


app = create_app()
