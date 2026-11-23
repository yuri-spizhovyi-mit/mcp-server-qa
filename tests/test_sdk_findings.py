"""Findings F-01..F-03: what a bare `MCPServer` (mcp 2.3.0) does where it departs from the spec.

These are characterization tests: they pin the SDK's current behaviour, so they pass on the
SDK we use, and `energy_mcp` works around each one (`energy_mcp/guard.py`). They are how we
notice an SDK upgrade that fixes or changes the behaviour: the pin fails, and the finding in
`docs/findings.md` and its workaround get revisited. They never start `energy_mcp`.
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from servers import MODES

pytestmark = pytest.mark.anyio


def bare() -> MCPServer:
    server = MCPServer("bare")

    @server.tool()
    def count(n: int) -> int:
        """Return n."""
        return n

    @server.prompt()
    def p(x: str) -> str:
        """Say x."""
        return x

    return server


@pytest.mark.req("SRV-04")
@pytest.mark.parametrize("mode", MODES)
async def test_f01_bare_sdk_answers_unknown_tool_with_is_error(mode: str) -> None:
    async with Client(bare(), mode=mode) as client:
        result = await client.call_tool("nosuch", {})
    assert result.is_error is True
    assert result.content[0].text == "Unknown tool: nosuch"


@pytest.mark.req("SRV-04")
@pytest.mark.parametrize("mode", MODES)
async def test_f02_bare_sdk_coerces_and_drops_arguments(mode: str) -> None:
    async with Client(bare(), mode=mode) as client:
        for arguments in ({"n": "2"}, {"n": True}, {"n": 1, "zzz": 1}):
            result = await client.call_tool("count", arguments)
            assert result.is_error is False, arguments


@pytest.mark.req("SRV-06")
@pytest.mark.parametrize("mode", MODES)
async def test_f03_bare_sdk_missing_prompt_argument_is_not_32602(mode: str) -> None:
    async with Client(bare(), mode=mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt("p", {})
    assert caught.value.error.code != INVALID_PARAMS
