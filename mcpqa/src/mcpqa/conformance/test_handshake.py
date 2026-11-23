"""How a client and server agree on a revision: `initialize` (2025-11-25) and `server/discover` (2026-07-28).

Spec: lifecycle, "Version Negotiation" (2025-11-25); versioning, "Protocol Version
Negotiation", the `_meta` envelope, and Streamable HTTP "Server Validation" (2026-07-28).
"""

from __future__ import annotations

from typing import Any

import pytest

from mcpqa.expect import error_of, result_of
from mcpqa.plugin import not_applicable
from mcpqa.protocol import CLIENT_INFO, LEGACY, MODERN, Session, envelope, modern_headers
from mcpqa.wire import HttpWire, Reply

UNKNOWN_VERSION = "1900-01-01"


def initialize(wire: Any, version: str) -> Reply:
    return wire.exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": version, "capabilities": {}, "clientInfo": CLIENT_INFO}})


@pytest.mark.mcpqa_revisions(LEGACY)
def test_legacy_initialize(revision: str, session: Session, fresh_wire: Any) -> None:
    """MUST answer with the requested version when it supports it, plus serverInfo and capabilities."""
    result = result_of(initialize(fresh_wire, LEGACY))
    assert result.get("protocolVersion") == LEGACY
    info = result.get("serverInfo")
    assert isinstance(info, dict) and isinstance(info.get("name"), str) and isinstance(info.get("version"), str), (
        f"serverInfo must have a string name and version, got {info!r}")
    assert isinstance(result.get("capabilities"), dict)


@pytest.mark.mcpqa_revisions(LEGACY)
def test_legacy_negotiates_unknown_version(revision: str, session: Session, new_wire: Any) -> None:
    """PROTO-D2: an unsupported requested version MUST get "another protocol version it supports".

    Any version may come back (a server can serve versions mcpqa doesn't know), but not the
    impossible one that was asked for, and asking again for the version it named must give
    that same version: proof that the server serves it.
    """
    answered = result_of(initialize(new_wire(), UNKNOWN_VERSION)).get("protocolVersion")
    assert isinstance(answered, str) and answered, f"no protocolVersion in the answer: {answered!r}"
    assert answered != UNKNOWN_VERSION, f"the server echoed {UNKNOWN_VERSION}, a version no one serves"
    again = result_of(initialize(new_wire(), answered)).get("protocolVersion")
    assert again == answered, f"the server named {answered} but answers {again!r} when asked for it"


@pytest.mark.mcpqa_revisions(MODERN)
def test_discover(revision: str, session: Session) -> None:
    """Servers MUST implement server/discover."""
    result = result_of(session.request("server/discover"))
    assert result.get("resultType") == "complete"
    assert MODERN in (result.get("supportedVersions") or []), (
        f"supportedVersions must include {MODERN}, got {result.get('supportedVersions')!r}")
    assert isinstance(result.get("capabilities"), dict)


@pytest.mark.mcpqa_revisions(MODERN)
def test_unsupported_version_is_32022(revision: str, session: Session, fresh_wire: Any) -> None:
    """PROTO-D1: -32022 with data.supported and data.requested; HTTP 400."""
    message = {"jsonrpc": "2.0", "id": session.next_id(), "method": "tools/list",
               "params": {"_meta": envelope(UNKNOWN_VERSION)}}
    reply = session.wire.exchange(message, modern_headers("tools/list", version=UNKNOWN_VERSION))
    data = error_of(reply, -32022).get("data")
    assert isinstance(data, dict) and data.get("requested") == UNKNOWN_VERSION, data
    supported = data.get("supported")
    assert isinstance(supported, list) and MODERN in supported, supported
    if LEGACY in supported:  # a listed version must really be served
        result_of(initialize(fresh_wire, LEGACY))
    if isinstance(session.wire, HttpWire):
        assert reply.status == 400


@pytest.mark.mcpqa_revisions(MODERN)
def test_missing_envelope_is_32602(revision: str, session: Session) -> None:
    """A request missing a required _meta field is malformed: MUST be -32602 (HTTP 400)."""
    message = {"jsonrpc": "2.0", "id": session.next_id(), "method": "tools/list", "params": {}}
    reply = session.wire.exchange(message, modern_headers("tools/list"))
    error_of(reply, -32602)
    if isinstance(session.wire, HttpWire):
        assert reply.status == 400


@pytest.mark.mcpqa_revisions(MODERN)
def test_header_mismatch_is_32020(revision: str, session: Session) -> None:
    """A header that doesn't match the body is HTTP 400 with -32020 (HeaderMismatch)."""
    if not isinstance(session.wire, HttpWire):
        not_applicable("headers exist only over HTTP")
    message, headers = session.message("tools/list")
    reply = session.wire.exchange(message, {**headers, "mcp-method": "prompts/list"})
    error_of(reply, -32020)
    assert reply.status == 400
