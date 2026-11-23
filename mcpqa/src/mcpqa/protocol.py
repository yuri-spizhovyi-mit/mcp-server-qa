"""What each protocol revision adds to a request, and a session that opens either one.

2026-07-28: no handshake; every request carries its version and client capabilities in
`params._meta` and, over HTTP, the `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` headers.
2025-11-25: `initialize`, then `notifications/initialized`; over HTTP the server may hand out
an `Mcp-Session-Id`, and later requests carry it and `MCP-Protocol-Version`.
"""

from __future__ import annotations

import base64
import itertools
from typing import Any

from mcpqa import __version__
from mcpqa.wire import HttpWire, Reply, StdioWire

LEGACY = "2025-11-25"
MODERN = "2026-07-28"
REVISIONS = (LEGACY, MODERN)
CLIENT_INFO = {"name": "mcpqa", "version": __version__}


# Answers that mean "this revision is not served here", as opposed to "this server is broken" (C1).
# A 2026-07-28 server refuses an unknown version with -32022; an older server doesn't know
# server/discover (-32601). A 2025-11-25 refusal of initialize uses -32022, -32601 or the
# lifecycle page's -32602 ("Unsupported protocol version").
NOT_SERVED = {MODERN: frozenset({-32022, -32601}), LEGACY: frozenset({-32022, -32601, -32602})}


class ProtocolError(Exception):
    """The server answered something other than what the protocol requires."""

    def __init__(self, reply: Reply, reason: str | None = None) -> None:
        self.reply = reply
        error = (reply.message or {}).get("error")
        self.code = error.get("code") if isinstance(error, dict) else None
        shown = error if error is not None else reply.message
        status = f"HTTP {reply.status} " if reply.status is not None else ""
        super().__init__(reason or f"the server answered {status}{shown!r}")


class NotServed(ProtocolError):
    """The target doesn't serve this revision: its checks are not applicable."""


class AuthRefused(ProtocolError):
    """HTTP 401 or 403: the bearer token is missing or refused."""


def envelope(version: str = MODERN) -> dict[str, Any]:
    return {"io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO}


def _header_value(value: str) -> str:
    if value.isascii() and value.isprintable():
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="  # 2026-07-28 sentinel form


def modern_headers(method: str, name: str | None = None, version: str = MODERN) -> dict[str, str]:
    headers = {"mcp-protocol-version": version, "mcp-method": method}
    if name is not None:
        headers["mcp-name"] = _header_value(name)
    return headers


class Session:
    def __init__(self, wire: StdioWire | HttpWire, revision: str) -> None:
        if revision not in REVISIONS:
            raise ValueError(f"unknown revision {revision}")
        self.wire, self.revision = wire, revision
        self.capabilities: dict[str, Any] = {}
        self._ids = itertools.count(1)

    def next_id(self) -> int:
        return next(self._ids)

    def open(self) -> dict[str, Any]:
        """The discover result (2026-07-28) or the initialize result (2025-11-25); ProtocolError if refused."""
        if self.revision == MODERN:
            reply = self.request("server/discover")
        else:
            reply = self.wire.exchange({"jsonrpc": "2.0", "id": self.next_id(), "method": "initialize", "params": {
                "protocolVersion": LEGACY, "capabilities": {}, "clientInfo": CLIENT_INFO}})
        if reply.status in (401, 403):
            raise AuthRefused(reply, f"HTTP {reply.status}")
        result = (reply.message or {}).get("result")
        if not isinstance(result, dict):
            error = ProtocolError(reply)
            raise NotServed(reply, str(error)) if error.code in NOT_SERVED[self.revision] else error
        if self.revision == LEGACY and result.get("protocolVersion") != LEGACY:
            raise NotServed(reply, f"initialize negotiated {result.get('protocolVersion')}")
        self.capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
        if self.revision == LEGACY:
            if isinstance(self.wire, HttpWire):
                session_id = reply.headers.get("mcp-session-id")
                self.wire.session_headers = {"mcp-protocol-version": result["protocolVersion"],
                                             **({"mcp-session-id": session_id} if session_id else {})}
            self.wire.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def message(self, method: str, params: dict[str, Any] | None = None, *, with_envelope: bool = True,
                request_id: int | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        """The request and its HTTP headers, as this revision sends them."""
        params = dict(params or {})
        headers: dict[str, str] = {}
        if self.revision == MODERN:
            if with_envelope:
                params["_meta"] = {**envelope(), **params.get("_meta", {})}
            name = params.get("name") if method in ("tools/call", "prompts/get") else params.get("uri")
            headers = modern_headers(method, name if isinstance(name, str) else None)
        message = {"jsonrpc": "2.0", "id": self.next_id() if request_id is None else request_id, "method": method}
        if params:
            message["params"] = params
        return message, headers

    def request(self, method: str, params: dict[str, Any] | None = None, **options: Any) -> Reply:
        message, headers = self.message(method, params, **options)
        return self.wire.exchange(message, headers)
