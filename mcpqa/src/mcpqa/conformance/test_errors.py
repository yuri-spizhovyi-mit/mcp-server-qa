"""JSON-RPC error codes: what a server answers to a message it can't read or a call it can't serve.

Spec: base protocol "Error Codes" and the schema's error definitions (2026-07-28); JSON-RPC 2.0
(`id: null` when the request's id can't be determined); Streamable HTTP "Protocol Version
Header" (an unknown method is HTTP 404 on 2026-07-28).
"""

from __future__ import annotations

from mcpqa.expect import SHOULD, error_of
from mcpqa.plugin import not_applicable
from mcpqa.protocol import MODERN, Session, modern_headers
from mcpqa.wire import HttpWire


def _headers(session: Session, method: str = "tools/list") -> dict[str, str]:
    return modern_headers(method) if session.revision == MODERN else {}


def test_parse_error_is_32700(revision: str, session: Session) -> None:
    reply = session.wire.exchange(b'{"jsonrpc":"2.0","id":1,', _headers(session), accept_null_id=True)
    error_of(reply, -32700)
    assert reply.message.get("id", "missing") is None, f"id must be null, got {reply.message.get('id', 'missing')!r}"
    if isinstance(session.wire, HttpWire):
        assert reply.status == 400


def test_invalid_request_is_32600(revision: str, session: Session) -> None:
    request_id = session.next_id()
    reply = session.wire.exchange({"jsonrpc": "2.0", "id": request_id}, _headers(session), accept_null_id=True)
    error_of(reply, -32600)
    assert reply.message.get("id", "missing") in (request_id, None)
    if isinstance(session.wire, HttpWire):
        assert reply.status == 400


def test_unknown_method_is_32601(revision: str, session: Session) -> None:
    reply = session.request("mcpqa/no-such-method")
    error_of(reply, -32601)
    if isinstance(session.wire, HttpWire) and revision == MODERN:
        assert reply.status == 404


def test_unknown_tool_is_32602(revision: str, session: Session) -> None:
    """SHOULD: the tools page gives an unknown tool as its example of a protocol error (PROTO-D5)."""
    if "tools" not in session.capabilities:
        not_applicable("the target declares no tools")
    error_of(session.request("tools/call", {"name": "mcpqa-no-such-tool", "arguments": {}}), -32602, SHOULD)
