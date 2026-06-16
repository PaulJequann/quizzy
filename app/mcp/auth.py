from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastmcp.server.auth.providers.debug import DebugTokenVerifier


class StaticBearerTokenVerifier(DebugTokenVerifier):
    """Static bearer-token verifier for private hosted deployments."""

    def __init__(
        self,
        validate: Callable[[str], bool] | Callable[[str], Awaitable[bool]],
        *,
        client_id: str = "quizzy-mcp-client",
        scopes: list[str] | None = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(
            validate=validate,
            client_id=client_id,
            scopes=scopes,
            required_scopes=required_scopes,
        )
