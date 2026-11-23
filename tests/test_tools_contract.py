"""SRV-02: every tool's output on the seeded world matches its outputSchema, on both revisions."""

from __future__ import annotations

import json

import pytest

from energy_mcp.server import TOOL_NAMES, build_server
from energy_mcp.world import build_world
from servers import MODES, connect, validate, writer

pytestmark = [pytest.mark.req("SRV-02"), pytest.mark.anyio, pytest.mark.parametrize("mode", MODES)]


async def test_tools_listed_with_object_schemas_that_forbid_extras(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == set(TOOL_NAMES)
    for tool in tools:
        assert tool.input_schema["type"] == "object", tool.name
        assert tool.input_schema["additionalProperties"] is False, tool.name  # SRV-D2
        assert tool.output_schema, tool.name
        assert tool.description, tool.name


async def test_every_tool_output_matches_its_output_schema(mode: str) -> None:
    world = build_world()
    first_open = next(a.alert_id for a in world.alerts if a.status == "open")
    calls = {
        "list_meters": {},
        "get_readings": {"meter_id": "MTR-0001"},
        "list_alerts": {},
        "acknowledge_alert": {"alert_id": first_open},
    }
    assert set(calls) == set(TOOL_NAMES)
    async with connect(build_server(world, identity=writer()), mode) as client:
        schemas = {t.name: t.output_schema for t in (await client.list_tools()).tools}
        for name, arguments in calls.items():
            result = await client.call_tool(name, arguments)
            assert result.is_error is False, (name, result.content)
            validate(result.structured_content, schemas[name])
            assert json.loads(result.content[0].text) == result.structured_content, name


async def test_tool_annotations_match_behaviour(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        hints = {t.name: t.annotations for t in (await client.list_tools()).tools}
    for reader in ("list_meters", "get_readings", "list_alerts"):
        assert hints[reader].read_only_hint is True, reader
        assert hints[reader].open_world_hint is False, reader
    ack = hints["acknowledge_alert"]
    assert (ack.read_only_hint, ack.destructive_hint, ack.idempotent_hint, ack.open_world_hint) == (False, False, True, False)
