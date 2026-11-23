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

from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INTERNAL_ERROR, METHOD_NOT_FOUND
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
    answer_malformed: bool = True
    strict_unknown_tool: bool = True
    reject_list_cursors: bool = True
    startup_stdout: str | None = None
    # What each expected failure's message must say (the right reason), and the checks that
    # this copy legitimately turns "not applicable".
    expected_message: str = ""
    expected_skips: frozenset[str] = frozenset()

    def server_options(self) -> dict[str, Any]:
        """Keyword arguments for `build_server`."""
        return {"extra_middleware": self.middleware, "strict_unknown_tool": self.strict_unknown_tool,
                "reject_list_cursors": self.reject_list_cursors}


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


async def _internal_for_unknown_method(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    try:
        return await call_next(ctx)
    except MCPError as error:
        if error.error.code == METHOD_NOT_FOUND:
            raise MCPError(code=INTERNAL_ERROR, message="Internal error") from None
        raise


def _without_tools_capability(message: Message) -> Message:
    result = _result(message)
    if result is not None and isinstance(result.get("capabilities"), dict):
        result["capabilities"].pop("tools", None)
    return message


async def _endless_tool_pages(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    """tools/list always says there is another page, and that page is the same one again."""
    params = ctx.params if isinstance(ctx.params, dict) else {}
    if ctx.method != "tools/list" or params.get("cursor") not in (None, "again"):
        return await call_next(ctx)
    ctx.params = {k: v for k, v in params.items() if k != "cursor"}
    result = await call_next(ctx)
    if isinstance(result, dict):
        return {**result, "nextCursor": "again"}
    return result.model_copy(update={"next_cursor": "again"})


async def _endless_distinct_pages(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    """tools/list pages forever: the real tools, then empty pages, each with a new cursor."""
    params = ctx.params if isinstance(ctx.params, dict) else {}
    cursor = params.get("cursor")
    if ctx.method != "tools/list" or not (cursor is None or (isinstance(cursor, str) and cursor.startswith("p"))):
        return await call_next(ctx)
    page = int(cursor[1:]) if cursor else 0
    ctx.params = {k: v for k, v in params.items() if k != "cursor"}
    result = await call_next(ctx)
    update = {"nextCursor": f"p{page + 1}", **({"tools": []} if page else {})}
    if isinstance(result, dict):
        return {**result, **update}
    return result.model_copy(update={"next_cursor": update["nextCursor"], **({"tools": []} if page else {})})


async def _crash_on_large_argument(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    params = ctx.params if isinstance(ctx.params, dict) else {}
    arguments = params.get("arguments")
    if ctx.method == "tools/call" and isinstance(arguments, dict) and any(
            isinstance(v, str) and len(v) > 100_000 for v in arguments.values()):
        raise MCPError(code=INTERNAL_ERROR, message="Internal error")
    return await call_next(ctx)


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
        frozenset({"test_legacy_initialize"}), outgoing=_without_server_info, expected_message='serverInfo must have'),
    "echoes-unknown-version": Variant(
        "initialize answers with whatever version the client asked for", "PROTO-02",
        frozenset({"test_legacy_negotiates_unknown_version"}), middleware=(_echo_requested_version,), expected_message='echoed 1900-01-01'),
    "discover-without-versions": Variant(
        "server/discover has no supportedVersions", "PROTO-02",
        frozenset({"test_discover"}), outgoing=_without_versions, expected_message='supportedVersions must include'),
    "wrong-unsupported-version-error": Variant(
        "an unsupported version gets -32602 instead of -32022", "PROTO-02",
        frozenset({"test_unsupported_version_is_32022"}), outgoing=_wrong_version_error, expected_message="expected error -32022, got {'code': -32602"),
    "accepts-missing-envelope": Variant(
        "a request without the _meta envelope gets a result", "PROTO-03",
        frozenset({"test_missing_envelope_is_32602"}), outgoing=_accepts_missing_envelope, expected_message="expected error -32602, got HTTP None: {'jsonrpc': '2.0', 'id'"),
    "ignores-header-mismatch": Variant(
        "Mcp-Method is overwritten with the body's method, so a mismatch is never refused", "PROTO-03",
        frozenset({"test_header_mismatch_is_32020"}), asgi=_ignore_header_mismatch, transport="http", expected_message='expected error -32020'),
    "drops-malformed": Variant(
        "the stdio front is off: malformed lines get no answer (F-13)", "PROTO-05",
        frozenset({"test_parse_error_is_32700", "test_invalid_request_is_32600"}), answer_malformed=False, expected_message='no message from the server within'),
    "internal-for-unknown-method": Variant(
        "an unknown method gets -32603 instead of -32601", "PROTO-05",
        frozenset({"test_unknown_method_is_32601"}), middleware=(_internal_for_unknown_method,), expected_message="expected error -32601, got {'code': -32603"),
    "unknown-tool-is-result": Variant(
        "an unknown tool gets the SDK's isError result instead of -32602 (F-01)", "PROTO-05",
        frozenset({"test_unknown_tool_is_32602"}), strict_unknown_tool=False, expected_message='Unknown tool: mcpqa-no-such-tool'),
    "undeclared-tools": Variant(
        "tools are served but not declared in the capabilities", "PROTO-04",
        frozenset({"test_declared_capabilities_work"}), outgoing=_without_tools_capability, expected_message='SHOULD (the spec recommends this; it does not require it): expected error -32601', expected_skips=frozenset({"test_unknown_tool_is_32602", "test_large_argument_is_handled"})),
    "accepts-any-cursor": Variant(
        "a cursor the server never issued is accepted and gives the first page (F-14)", "PROTO-06",
        frozenset({"test_invalid_cursor_is_32602"}), reject_list_cursors=False, expected_message='SHOULD (the spec recommends this; it does not require it): expected error -32602'),
    "endless-pages": Variant(
        "tools/list hands out a nextCursor that returns the same page forever", "PROTO-06",
        frozenset({"test_pagination_is_complete"}), middleware=(_endless_tool_pages,), expected_message='came back on a later page'),
    "endless-distinct-pages": Variant(
        "tools/list hands out a new nextCursor on every page, forever", "PROTO-06",
        frozenset({"test_pagination_is_complete"}), middleware=(_endless_distinct_pages,), expected_message='did not end after 1000 pages'),
    "crashes-on-large-argument": Variant(
        "a tool call with a string argument over 100 000 characters is -32603", "PROTO-07",
        frozenset({"test_large_argument_is_handled"}), middleware=(_crash_on_large_argument,), expected_message='with a 1 MiB'),
    "prints-to-stdout": Variant(
        "a startup line on stdout that is not an MCP message", "PROTO-07",
        frozenset({"test_stdout_carries_only_messages"}), startup_stdout="energy_mcp starting", expected_message="not MCP messages on stdout: ['energy_mcp starting']"),
    "prints-json-to-stdout": Variant(
        "a structured log line on stdout: valid JSON, but not an MCP message", "PROTO-07",
        frozenset({"test_stdout_carries_only_messages"}),
        startup_stdout='{"level": "info", "msg": "energy_mcp starting"}', expected_message='not MCP messages on stdout: [\'{"level": "info"'),
}
