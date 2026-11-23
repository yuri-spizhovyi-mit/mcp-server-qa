"""The HTTP edge: one ASGI layer in front of the SDK's Streamable HTTP app (M2, approach A).

For every request except the RFC 9728 metadata it answers, in this order:
401 (no usable bearer token), 429 (rate limit per principal), 400 (a body it can't read
for certain), 403 (the call's scope is missing). Only then does the SDK see the request,
with the token's principal in `scope["user"]` (the SDK binds sessions to it) and in the
SDK's auth context (where `ScopeGuard` and the tools find the caller).

Every challenge is built from fixed text and from scope strings out of `scopes.py`, never
from what the caller sent (SRV-D1). The body is read once, bounded, and replayed unchanged.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken, TokenVerifier, principal_components
from mcp.shared.auth import ProtectedResourceMetadata
from starlette.authentication import AuthCredentials
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from energy_mcp.access import NO_ACCESS
from energy_mcp.ratelimit import RateLimiter
from energy_mcp.scopes import BASE_SCOPES, READ_SCOPES, missing, needed
from energy_mcp.server import build_server, utc_now
from energy_mcp.tokens import JwtVerifier, TokenSettings
from energy_mcp.world import World

MAX_BODY = 4 * 1024 * 1024
UNREADABLE = object()  # a body that is not one well-formed JSON object
DISCONNECTED = object()  # the client hung up before the body was complete


def metadata_url(resource_url: str) -> str:
    """RFC 9728 §3.1: the well-known path, followed by the resource's own path."""
    parts = urlsplit(resource_url)
    return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{parts.path.rstrip('/')}"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):  # parsers differ on which duplicate wins: never guess
        raise ValueError("duplicate key")
    return dict(pairs)


def _parse(body: bytes) -> Any:
    try:
        message = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError):
        return UNREADABLE
    return message if isinstance(message, dict) else UNREADABLE


async def _respond(send: Send, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    payload = json.dumps(body).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    raw += [(k.lower().encode("latin-1"), v.encode("ascii")) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": payload})


class AuthEdge:
    def __init__(self, app: ASGIApp, verifier: TokenVerifier, *, resource_url: str, issuer: str,
                 limiter: RateLimiter | None = None, max_body: int = MAX_BODY) -> None:
        self.app, self.verifier, self.limiter, self.max_body = app, verifier, limiter, max_body
        self.resource_url, self.issuer = resource_url, issuer
        self.metadata_url = metadata_url(resource_url)
        self.metadata_path = urlsplit(self.metadata_url).path
        self.metadata = ProtectedResourceMetadata(
            resource=resource_url, authorization_servers=[issuer], scopes_supported=list(BASE_SCOPES),
            bearer_methods_supported=["header"]).model_dump(mode="json", exclude_none=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan: the SDK app starts its session manager
            await self.app(scope, receive, send)
            return
        if scope["path"] == self.metadata_path and scope["method"] in ("GET", "HEAD"):
            await _respond(send, 200, self.metadata, {"cache-control": "public, max-age=3600"})
            return
        body = b""
        if scope["method"] == "POST":
            body = await self._read_body(receive)
            if body is DISCONNECTED:  # nobody is listening, and half a request is no request
                return
            if body is None:
                await _respond(send, 413, {"error": "too_large", "error_description": "The request body is too large"})
                return
        message = _parse(body) if scope["method"] == "POST" else {}
        needs = frozenset() if message is UNREADABLE else needed(message.get("method"), message.get("params"))
        credentials = self._bearer(scope)
        token = None if credentials is None else await self.verifier.verify_token(credentials)
        if token is None:
            await self._unauthorized(send, needs, sent=credentials is not None)
            return
        if self.limiter is not None and (retry := self.limiter.hit(principal_components(token))) is not None:
            await _respond(send, 429, {"error": "rate_limited", "error_description": "Too many requests"},
                           {"retry-after": str(max(1, math.ceil(retry)))})
            return
        if message is UNREADABLE:
            await _respond(send, 400, {"jsonrpc": "2.0", "id": None,
                                       "error": {"code": -32700, "message": "Parse error: one JSON object expected"}})
            return
        if lacking := missing(token.scopes, needs):
            await _respond(send, 403, {"error": "insufficient_scope", "error_description": "This call needs more scope"},
                           {"www-authenticate": f'Bearer error="insufficient_scope", scope="{" ".join(lacking)}", '
                                                f'resource_metadata="{self.metadata_url}", '
                                                'error_description="This call needs more scope"'})
            return
        await self._hand_on(scope, receive, send, body, token)

    async def _read_body(self, receive: Receive) -> bytes | object | None:
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return DISCONNECTED
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    def _bearer(self, scope: Scope) -> str | None:
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, credentials = value.decode("latin-1").partition(" ")
                return credentials.strip() if scheme.lower() == "bearer" else None
        return None  # a token in the query string is not looked at (MCP: header only)

    async def _unauthorized(self, send: Send, needs: frozenset[str], *, sent: bool) -> None:
        asked = " ".join(sorted(needs or READ_SCOPES))
        if sent:  # RFC 6750 §3: an error code only when credentials were sent
            header = (f'Bearer error="invalid_token", error_description="The access token is invalid or expired", '
                      f'resource_metadata="{self.metadata_url}", scope="{asked}"')
            body = {"error": "invalid_token", "error_description": "The access token is invalid or expired"}
        else:
            header = f'Bearer resource_metadata="{self.metadata_url}", scope="{asked}"'
            body = {"error": "unauthorized", "error_description": "A bearer token is required"}
        await _respond(send, 401, body, {"www-authenticate": header})

    async def _hand_on(self, scope: Scope, receive: Receive, send: Send, body: bytes, token: AccessToken) -> None:
        user = AuthenticatedUser(token)
        scope = {**scope, "user": user, "auth": AuthCredentials(token.scopes)}
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        reset = auth_context_var.set(user)
        try:
            await self.app(scope, replay, send)
        finally:
            auth_context_var.reset(reset)


def build_http_app(world: World, *, settings: TokenSettings, clock: Callable[[], float],
                   wall_clock: Callable[[], datetime] = utc_now, rate_limit: int = 60, rate_window: float = 60.0,
                   max_body: int = MAX_BODY, host: str = "127.0.0.1") -> tuple[AuthEdge, MCPServer]:
    """The server behind the edge has no fallback scopes (AUTH-D6): bypass the edge and nothing is allowed."""
    server = build_server(world, clock=wall_clock, identity=NO_ACCESS)
    # host: the SDK's DNS-rebinding check accepts only this host's names in the Host header
    edge = AuthEdge(server.streamable_http_app(host=host), JwtVerifier(settings, clock), resource_url=settings.audience,
                    issuer=settings.issuer, limiter=RateLimiter(rate_limit, rate_window, clock), max_body=max_body)
    return edge, server
