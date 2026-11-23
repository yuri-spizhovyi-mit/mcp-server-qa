"""Assertions shared by the conformance checks: a reply is a result, or a given JSON-RPC error."""

from __future__ import annotations

from typing import Any

from mcpqa.wire import Reply


def result_of(reply: Reply) -> dict[str, Any]:
    result = (reply.message or {}).get("result")
    assert isinstance(result, dict), f"expected a result, got HTTP {reply.status}: {reply.message}"
    return result


SHOULD = "SHOULD (the spec recommends this; it does not require it)"


def error_of(reply: Reply, code: int, level: str = "MUST") -> dict[str, Any]:
    """`level` says how strongly the spec asks for this answer; a SHOULD says so in the failure (PROTO-D5)."""
    error = (reply.message or {}).get("error")
    assert isinstance(error, dict), f"{level}: expected error {code}, got HTTP {reply.status}: {reply.message}"
    assert error.get("code") == code, f"{level}: expected error {code}, got {error}"
    return error
