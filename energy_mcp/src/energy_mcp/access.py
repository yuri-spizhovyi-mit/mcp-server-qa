"""Who is calling, and the scope check that runs on every transport (AUTH-D3, AUTH-D6).

Over HTTP the caller is the verified token, which the edge puts in the SDK's auth context.
Without HTTP (stdio, in-process) it is the server's fallback `Identity`: from the
environment for stdio, `NO_ACCESS` for a server built for HTTP. `ScopeGuard` enforces the
same policy as the edge, as the second line behind it and the only line on stdio.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.scopes import BASE_SCOPES, READ_SCOPES, is_known, missing, needed
from energy_mcp.tokens import PRINCIPAL


@dataclass(frozen=True)
class Identity:
    operator: str
    scopes: frozenset[str]


NO_ACCESS = Identity("unauthenticated", frozenset())


def identity_from_env(env: Mapping[str, str]) -> Identity:
    """ENERGY_MCP_OPERATOR (default local-operator) and ENERGY_MCP_SCOPES (default read-only)."""
    operator = env.get("ENERGY_MCP_OPERATOR", "local-operator")
    scopes = frozenset(env.get("ENERGY_MCP_SCOPES", " ".join(sorted(READ_SCOPES))).split())
    if not PRINCIPAL.fullmatch(operator):
        raise ValueError("ENERGY_MCP_OPERATOR must be 1-64 characters from A-Z a-z 0-9 . _ -")
    if not all(is_known(s) for s in scopes):  # the bad value is not echoed (SRV-D1)
        raise ValueError(f"ENERGY_MCP_SCOPES has an unknown scope; allowed: {' '.join(BASE_SCOPES)}, "
                         "alerts:write:ALR-NNNN")
    return Identity(operator, scopes)


def current_identity(fallback: Identity) -> Identity:
    token = get_access_token()
    if token is None or token.subject is None:
        return fallback
    return Identity(token.subject, frozenset(token.scopes))


class ScopeGuard:
    """SDK middleware: refuse a call whose caller lacks its scope, before arguments are checked."""

    def __init__(self, fallback: Identity) -> None:
        self.fallback = fallback

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        lacking = missing(current_identity(self.fallback).scopes, needed(ctx.method, ctx.params))
        if not lacking:
            return await call_next(ctx)
        text = f"Not allowed: this call needs scope {' '.join(lacking)}"
        if ctx.method == "tools/call":
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
        raise MCPError(code=INVALID_PARAMS, message=text)
