"""SRV-07: stdio and Streamable HTTP, on both revisions, behave like the in-process server."""

from __future__ import annotations

import contextlib

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.__main__ import parse_args
from energy_mcp.server import TOOL_NAMES
from servers import MODES, free_port, http_server, stdio_params, validate

pytestmark = pytest.mark.req("SRV-07")


def test_cli_defaults_to_stdio_and_localhost() -> None:
    """Started with no flags, the server must not listen on every interface."""
    args = parse_args([])
    assert (args.transport, args.host, args.port, args.seed) == ("stdio", "127.0.0.1", 8000, 42)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_transport_behaves_like_in_process(transport: str, mode: str) -> None:
    with contextlib.ExitStack() as stack:
        target = stdio_params() if transport == "stdio" else stack.enter_context(http_server(free_port()))
        async with Client(target, mode=mode) as client:
            assert client.protocol_version == ("2025-11-25" if mode == "legacy" else mode)
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == set(TOOL_NAMES)
            ok = await client.call_tool("list_meters", {})
            assert ok.is_error is False and len(ok.structured_content["meters"]) == 20
            validate(ok.structured_content, tools["list_meters"].output_schema)
            bad = await client.call_tool("get_readings", {"meter_id": "MTR-0001", "limit": "2"})
            assert bad.is_error is True and 'limit: must satisfy type "integer"' in bad.content[0].text
            with pytest.raises(MCPError) as caught:
                await client.call_tool("nosuch", {})
            assert caught.value.error.code == INVALID_PARAMS
