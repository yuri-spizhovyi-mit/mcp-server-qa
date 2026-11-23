"""Broken copies of energy_mcp, one planted bug each, selected with `--variant NAME` (tests only).

Each exists to prove that an `mcpqa` conformance check can fail: `expected_failures` names
the checks it must fail, and `tests/test_conformance.py` requires exactly that set. A bug is
planted with SDK middleware, with an `outgoing` rewrite in the stdio front, with a flag that
turns one of our own workarounds off, or with an ASGI wrapper for an HTTP-only check.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from starlette.types import ASGIApp, Receive, Scope, Send

Message = dict[str, Any]


@dataclass(frozen=True)
class Variant:
    description: str
    requirement: str
    expected_failures: frozenset[str]
    middleware: tuple[Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]], ...] = ()
    outgoing: Callable[[Message], Message | None] | None = None
    asgi: Callable[[ASGIApp], ASGIApp] | None = None
    transport: Literal["stdio", "http"] = "stdio"


def _result(message: Message) -> dict[str, Any] | None:
    result = message.get("result")
    return result if isinstance(result, dict) else None


def _error_code(message: Message) -> int | None:
    error = message.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _without_server_info(message: Message) -> Message:
    result = _result(message)
    if result is not None and "protocolVersion" in result:  # the initialize result
        result.pop("serverInfo", None)
    return message


def _without_versions(message: Message) -> Message:
    result = _result(message)
    if result is not None:
        result.pop("supportedVersions", None)
    return message


def _wrong_version_error(message: Message) -> Message:
    if _error_code(message) == -32022:
        message["error"] = {"code": -32602, "message": "Invalid params"}
    return message


def _accepts_missing_envelope(message: Message) -> Message:
    if _error_code(message) == -32602 and str(message["error"].get("message", "")).startswith("params._meta"):
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
    return message


async def _echo_requested_version(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    result = await call_next(ctx)
    if ctx.method == "initialize" and isinstance(ctx.params, dict):
        requested = ctx.params.get("protocolVersion")
        if isinstance(result, dict):
            return {**result, "protocolVersion": requested}
        return result.model_copy(update={"protocol_version": requested})
    return result


def _ignore_header_mismatch(app: ASGIApp) -> ASGIApp:
    """Overwrite Mcp-Method with the body's method before the server sees it, so no mismatch is ever found."""

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await app(scope, receive, send)
            return
        chunks, more = [], True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False) and message["type"] == "http.request"
        body = b"".join(chunks)
        try:
            method = json.loads(body).get("method")
        except (ValueError, AttributeError):
            method = None
        headers = [(k, v) for k, v in scope["headers"] if k != b"mcp-method"]
        if isinstance(method, str):
            headers.append((b"mcp-method", method.encode("latin-1", "replace")))
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await app({**scope, "headers": headers}, replay, send)

    return wrapped


VARIANTS: dict[str, Variant] = {
    "legacy-initialize-without-server-info": Variant(
        "the initialize result has no serverInfo", "PROTO-02",
        frozenset({"test_legacy_initialize"}), outgoing=_without_server_info),
    "echoes-unknown-version": Variant(
        "initialize answers with whatever version the client asked for", "PROTO-02",
        frozenset({"test_legacy_negotiates_unknown_version"}), middleware=(_echo_requested_version,)),
    "discover-without-versions": Variant(
        "server/discover has no supportedVersions", "PROTO-02",
        frozenset({"test_discover"}), outgoing=_without_versions),
    "wrong-unsupported-version-error": Variant(
        "an unsupported version gets -32602 instead of -32022", "PROTO-02",
        frozenset({"test_unsupported_version_is_32022"}), outgoing=_wrong_version_error),
    "accepts-missing-envelope": Variant(
        "a request without the _meta envelope gets a result", "PROTO-03",
        frozenset({"test_missing_envelope_is_32602"}), outgoing=_accepts_missing_envelope),
    "ignores-header-mismatch": Variant(
        "Mcp-Method is overwritten with the body's method, so a mismatch is never refused", "PROTO-03",
        frozenset({"test_header_mismatch_is_32020"}), asgi=_ignore_header_mismatch, transport="http"),
}
