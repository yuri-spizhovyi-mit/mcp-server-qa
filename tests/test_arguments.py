"""SRV-04: tool calls are checked strictly against the published inputSchema (SRV-D1, SRV-D2).

The bare SDK accepts "2" for an integer and drops unknown arguments (F-02), and answers an
unknown tool with a result instead of -32602 (F-01); `energy_mcp/guard.py` closes both.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.guard import ArgumentGuard
from energy_mcp.server import build_server
from energy_mcp.world import build_world
from servers import MODES, connect

pytestmark = [pytest.mark.req("SRV-04"), pytest.mark.anyio]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("tool", "args", "part"), [
    ("get_readings", {"meter_id": "MTR-0001", "limit": "2"}, 'limit: must satisfy type "integer"'),
    ("get_readings", {"meter_id": "MTR-0001", "limit": True}, 'limit: must satisfy type "integer"'),
    ("get_readings", {"meter_id": "MTR-0001", "limit": 0}, "limit: must satisfy minimum 1"),
    ("get_readings", {"meter_id": "MTR-0001", "limit": 169}, "limit: must satisfy maximum 168"),
    ("get_readings", {}, "(root): missing required property 'meter_id'"),
    ("get_readings", {"meter_id": "MTR-0001", "zzz": 1}, "(root): unexpected property 'zzz'"),
    ("acknowledge_alert", {"alert_id": "alr-1"}, 'alert_id: must satisfy pattern "^ALR-\\\\d{4}$"'),
    ("list_alerts", {"status": "closed"}, 'status: must satisfy enum ["open", "acknowledged"]'),
    ("list_meters", {"site_id": "site 2"}, 'site_id: must satisfy pattern "^SITE-\\\\d{2}$"'),
], ids=["string-for-int", "bool-for-int", "below-min", "above-max", "missing", "extra", "pattern",
        "enum-in-optional", "pattern-in-optional"])
async def test_schema_violations_are_tool_errors_naming_field_and_rule(mode: str, tool: str, args: dict,
                                                                       part: str) -> None:
    async with connect(build_server(), mode) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is True
    text = result.content[0].text
    assert text.startswith(f"Invalid arguments for tool {tool}: "), text
    assert part in text, text


@pytest.mark.parametrize("mode", MODES)
async def test_argument_errors_never_echo_the_value(mode: str) -> None:
    """The next model turn reads this text; a reflected value is a ready-made injection channel."""
    async with connect(build_server(), mode) as client:
        for args in ({"meter_id": "SENTINEL-7Q"}, {"meter_id": "MTR-0001", "limit": "SENTINEL-7Q"}):
            result = await client.call_tool("get_readings", args)
            assert result.is_error is True
            assert "SENTINEL-7Q" not in result.content[0].text, result.content[0].text


@pytest.mark.parametrize("mode", MODES)
async def test_unknown_tool_is_invalid_params(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("nosuch", {})
    assert (caught.value.error.code, caught.value.error.message) == (INVALID_PARAMS, "Unknown tool: nosuch")


async def test_non_object_arguments_are_invalid_params() -> None:
    """The SDK client can't send this, so the guard is called directly with the raw params."""
    guard = ArgumentGuard({"list_meters": {"type": "object", "properties": {}, "additionalProperties": False}})
    ctx = SimpleNamespace(method="tools/call", params={"name": "list_meters", "arguments": [1]})

    async def call_next(_ctx):
        pytest.fail("the guard passed malformed arguments on to the handler")

    with pytest.raises(MCPError) as caught:
        await guard(ctx, call_next)
    assert caught.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize("mode", MODES)
async def test_failed_calls_leave_the_world_unchanged(mode: str) -> None:
    world = build_world()
    before = world.digest()
    async with connect(build_server(world), mode) as client:
        assert (await client.call_tool("acknowledge_alert", {"alert_id": "ALR-1"})).is_error
        assert (await client.call_tool("acknowledge_alert", {"alert_id": "ALR-0999"})).is_error
        assert (await client.call_tool("acknowledge_alert", {"alert_id": "ALR-0001", "by": "x"})).is_error
        with pytest.raises(MCPError):
            await client.call_tool("acknowledge_alerts", {"alert_id": "ALR-0001"})
    assert world.digest() == before
