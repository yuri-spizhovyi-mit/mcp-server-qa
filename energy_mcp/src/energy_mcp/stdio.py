"""The stdio front (PROTO-D4): answer every line that isn't a valid message, then serve the rest.

mcp 2.3.0's stdio transport turns a malformed line into an exception in the session stream,
where it is dropped without an answer (F-13). This front sits between the transport's
streams and the server. A malformed line gets `-32700` (not JSON) or `-32600` (JSON, but not
a message), with the message's `id` when it can be read, else `null`. Everything else passes
unchanged. `outgoing` lets a broken copy (`variants.py`) rewrite or drop what the server sends.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
from mcp.server import MCPServer
from mcp.server.stdio import stdio_server
from mcp.shared._context_streams import create_context_streams
from mcp.shared.message import SessionMessage
from mcp_types import jsonrpc_message_adapter
from mcp_types.jsonrpc import INVALID_REQUEST, PARSE_ERROR, ErrorData, JSONRPCError

Outgoing = Callable[[dict[str, Any]], dict[str, Any] | None]


def answer_for(error: Exception) -> SessionMessage:
    """The JSON-RPC error that answers a line the transport could not read as a message."""
    details = error.errors() if hasattr(error, "errors") else []
    if any(d.get("type") == "json_invalid" for d in details):
        return SessionMessage(JSONRPCError(jsonrpc="2.0", id=None, error=ErrorData(code=PARSE_ERROR,
                                                                                 message="Parse error")))
    request_id = None
    for detail in details:
        value = detail.get("input")
        if isinstance(value, dict) and isinstance(value.get("id"), int | str) and not isinstance(value.get("id"), bool):
            request_id = value["id"]
            break
    return SessionMessage(JSONRPCError(jsonrpc="2.0", id=request_id,
                                       error=ErrorData(code=INVALID_REQUEST, message="Invalid Request")))


def _rewrite(message: SessionMessage, outgoing: Outgoing) -> SessionMessage | None:
    as_dict = message.message.model_dump(by_alias=True, exclude_unset=True, mode="json")
    rewritten = outgoing(as_dict)
    if rewritten is None:
        return None
    return SessionMessage(jsonrpc_message_adapter.validate_python(rewritten), metadata=message.metadata)


async def serve_stdio(server: MCPServer, *, outgoing: Outgoing | None = None, answer_malformed: bool = True) -> None:
    lowlevel = server._lowlevel_server  # the SDK's own run_stdio_async uses it the same way
    async with stdio_server() as (sdk_read, sdk_write):
        to_server, server_read = create_context_streams[SessionMessage | Exception](0)
        server_write, from_server = create_context_streams[SessionMessage](0)

        async def inbound() -> None:
            async with to_server:
                async for item in sdk_read:
                    if isinstance(item, Exception) and answer_malformed:
                        await sdk_write.send(answer_for(item))
                    else:
                        await to_server.send(item)

        async def outbound() -> None:
            async with from_server:
                async for message in from_server:
                    message = message if outgoing is None else _rewrite(message, outgoing)
                    if message is not None:
                        await sdk_write.send(message)

        async with anyio.create_task_group() as tg:
            tg.start_soon(inbound)
            tg.start_soon(outbound)
            async with server_write:
                await lowlevel.run(server_read, server_write, lowlevel.create_initialization_options())
            tg.cancel_scope.cancel()


def run_stdio(server: MCPServer, *, outgoing: Outgoing | None = None, answer_malformed: bool = True) -> None:
    anyio.run(lambda: serve_stdio(server, outgoing=outgoing, answer_malformed=answer_malformed))
