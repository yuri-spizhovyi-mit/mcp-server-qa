"""Payloads: a very large argument is handled cleanly, and stdio's stdout carries only MCP messages.

Spec: neither revision sets a message size limit, so a server must cope or refuse cleanly; a
crash (-32603) or silence fails. stdio: "The server MUST NOT write anything to its stdout that
is not a valid MCP message."
"""

from __future__ import annotations

from typing import Any

from mcpqa.expect import result_of
from mcpqa.plugin import not_applicable
from mcpqa.protocol import MODERN, Session
from mcpqa.wire import StdioWire

LARGE = "x" * (1024 * 1024)


def _read_only_tool_with_string(session: Session) -> tuple[str, str] | None:
    """A read-only tool only: this check never needs a write, so --allow-writes plays no part."""
    tools = result_of(session.request("tools/list")).get("tools") or []
    for tool in tools:
        annotations = tool.get("annotations") or {}
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        if annotations.get("readOnlyHint") is not True:
            continue
        for name, schema in properties.items():
            if isinstance(schema, dict) and (schema.get("type") == "string" or {"type": "string"} in (
                    schema.get("anyOf") or [])):
                return tool["name"], name
    return None


def test_large_argument_is_handled(revision: str, session: Session) -> None:
    if "tools" not in session.capabilities:
        not_applicable("the target declares no tools")
    found = _read_only_tool_with_string(session)
    if found is None:
        not_applicable("no read-only tool takes a string argument")
    tool, argument = found
    reply = session.request("tools/call", {"name": tool, "arguments": {argument: LARGE}})
    message: dict[str, Any] = reply.message or {}
    error = message.get("error")
    assert "result" in message or (isinstance(error, dict) and error.get("code") == -32602), (
        f"{tool} with a 1 MiB {argument}: {error or message}")
    result_of(session.request("tools/list"))  # and the server still answers


def test_stdout_carries_only_messages(revision: str, session: Session) -> None:
    if not isinstance(session.wire, StdioWire):
        not_applicable("stdout exists only over stdio")
    result_of(session.request("server/discover" if revision == MODERN else "ping"))  # every server answers these
    assert session.wire.noise == [], f"not MCP messages on stdout: {session.wire.noise[:3]}"
