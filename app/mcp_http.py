from __future__ import annotations

import hmac
from dataclasses import dataclass

from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.mcp.auth import StaticBearerTokenVerifier
from app.mcp.server import build_server


@dataclass(frozen=True)
class AsgiCall:
    scope: Scope
    receive: Receive
    send: Send


class HostedMcpRequestGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: set[str],
        max_request_bytes: int,
    ) -> None:
        self.app = app
        self.allowed_origins = allowed_origins
        self.max_request_bytes = max_request_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        call = AsgiCall(scope=scope, receive=receive, send=send)
        headers = scope.get("headers", [])
        origin = _header_value(headers, b"origin")
        if self._origin_is_blocked(origin):
            await _send_json(call, {"error": "Origin is not allowed."}, 403)
            return

        content_length = _header_value(headers, b"content-length")
        if content_length:
            try:
                request_bytes = int(content_length)
            except ValueError:
                await _send_json(
                    call,
                    {"error": "Invalid Content-Length header."},
                    400,
                )
                return
            if request_bytes > self.max_request_bytes:
                await _send_json(
                    call,
                    {"error": "MCP request body is too large."},
                    413,
                )
                return

        messages, body_is_too_large = await self._bounded_request_messages(receive)
        if body_is_too_large:
            await _send_json(
                call,
                {"error": "MCP request body is too large."},
                413,
            )
            return

        await self.app(scope, _replay_messages(messages, receive), send)

    def _origin_is_blocked(self, origin: str | None) -> bool:
        return bool(origin and self.allowed_origins and origin not in self.allowed_origins)

    async def _bounded_request_messages(self, receive: Receive) -> tuple[list[Message], bool]:
        messages: list[Message] = []
        body_bytes = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                return messages, False

            body_bytes += len(message.get("body", b""))
            if body_bytes > self.max_request_bytes:
                return messages, True

            messages.append(message)
            if not message.get("more_body", False):
                return messages, False


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for header_name, value in headers:
        if header_name.lower() == name:
            return value.decode("latin-1")
    return None


async def _send_json(
    call: AsgiCall,
    payload: dict[str, str],
    status_code: int,
) -> None:
    response = JSONResponse(payload, status_code=status_code)
    await response(call.scope, call.receive, call.send)


def _replay_messages(messages: list[Message], receive_after_replay: Receive) -> Receive:
    index = 0

    async def receive() -> Message:
        nonlocal index
        if index < len(messages):
            message = messages[index]
            index += 1
            return message
        return await receive_after_replay()

    return receive


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
        validate=lambda received: hmac.compare_digest(received, expected),
        required_scopes=["quizzy:mcp"],
        scopes=["quizzy:mcp"],
    )


def _allowed_origins(raw_origins: str) -> set[str]:
    return {origin.strip() for origin in raw_origins.split(",") if origin.strip()}


app = create_app()
