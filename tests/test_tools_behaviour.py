"""SRV-03: tool results are right against the seeded world, and writes change the world."""

from __future__ import annotations

import base64

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INTERNAL_ERROR

from energy_mcp.server import build_server
from energy_mcp.world import World, build_world
from servers import FakeClock, connect, writer

pytestmark = [pytest.mark.req("SRV-03"), pytest.mark.anyio]

MODE = "2026-07-28"  # protocol-revision coverage is SRV-02's and SRV-07's job


def _cursor_for(meter_id: str, offset: int) -> str:
    """A cursor in the server's format (urlsafe base64 of "<meter>:<offset>"), forged by the test."""
    return base64.urlsafe_b64encode(f"{meter_id}:{offset}".encode()).rstrip(b"=").decode()


async def call(world: World, name: str, arguments: dict, **server_kwargs):
    async with connect(build_server(world, **({"identity": writer()} | server_kwargs)), MODE) as client:
        return await client.call_tool(name, arguments)


async def test_list_meters_filters_by_site() -> None:
    world = build_world()
    result = await call(world, "list_meters", {"site_id": "SITE-02"})
    meters = result.structured_content["meters"]
    assert meters and {m["site_id"] for m in meters} == {"SITE-02"}
    assert len(meters) == sum(m.site_id == "SITE-02" for m in world.meters)
    assert len((await call(world, "list_meters", {})).structured_content["meters"]) == 20
    assert (await call(world, "list_meters", {"site_id": "SITE-99"})).structured_content["meters"] == []


async def test_readings_paginate_completely_in_order() -> None:
    world = build_world()
    pages, cursor = [], None
    async with connect(build_server(world), MODE) as client:
        while True:
            args = {"meter_id": "MTR-0003", "limit": 50} | ({"cursor": cursor} if cursor else {})
            page = (await client.call_tool("get_readings", args)).structured_content
            pages.append(page["readings"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert [len(p) for p in pages] == [50, 50, 50, 18]
    assert [r for p in pages for r in p] == [r.model_dump() for r in world.readings["MTR-0003"]]


async def test_bad_cursor_is_a_tool_error() -> None:
    world = build_world()
    async with connect(build_server(world), MODE) as client:
        first = await client.call_tool("get_readings", {"meter_id": "MTR-0001", "limit": 10})
        minted = first.structured_content["next_cursor"]
        forged = [_cursor_for("MTR-0001", offset) for offset in (0, -5, 168, 999)]  # in-format, out of range
        cases = [("MTR-0001", "garbage!!"), ("MTR-0002", minted), ("MTR-0001", "TVRSLTAwMDE6eA")]
        for meter_id, cursor in cases + [("MTR-0001", c) for c in forged]:
            result = await client.call_tool("get_readings", {"meter_id": meter_id, "cursor": cursor})
            assert result.is_error is True, cursor
            assert "Invalid cursor" in result.content[0].text, cursor


async def test_list_alerts_filters() -> None:
    world = build_world()
    open_alerts = (await call(world, "list_alerts", {"status": "open"})).structured_content["alerts"]
    assert [a["alert_id"] for a in open_alerts] == [a.alert_id for a in world.alerts if a.status == "open"]
    some_meter = world.alerts[0].meter_id
    on_meter = (await call(world, "list_alerts", {"meter_id": some_meter})).structured_content["alerts"]
    assert [a["alert_id"] for a in on_meter] == [a.alert_id for a in world.alerts if a.meter_id == some_meter]


async def test_acknowledge_changes_the_world_and_is_idempotent() -> None:
    world = build_world()
    alert_id = next(a.alert_id for a in world.alerts if a.status == "open")
    async with connect(build_server(world, clock=FakeClock(), identity=writer()), MODE) as client:
        first = (await client.call_tool("acknowledge_alert", {"alert_id": alert_id})).structured_content
        assert (first["status"], first["acknowledged_by"], first["acknowledged_at"]) == (
            "acknowledged", "op-1", "2026-01-12T09:00:00Z")
        stored = world.alert(alert_id)  # score the world, not the words
        assert (stored.status, stored.acknowledged_at) == ("acknowledged", "2026-01-12T09:00:00Z")
        again = (await client.call_tool("acknowledge_alert", {"alert_id": alert_id})).structured_content
        assert again == first  # SRV-D3: the clock moved on, the acknowledgement did not
        still_open = (await client.call_tool("list_alerts", {"status": "open"})).structured_content["alerts"]
    assert alert_id not in {a["alert_id"] for a in still_open}


async def test_unknown_ids_are_tool_errors() -> None:
    world = build_world()
    readings = await call(world, "get_readings", {"meter_id": "MTR-0099"})
    assert readings.is_error is True and "Unknown meter MTR-0099" in readings.content[0].text
    ack = await call(world, "acknowledge_alert", {"alert_id": "ALR-0999"})
    assert ack.is_error is True and "Unknown alert ALR-0999" in ack.content[0].text


async def test_tool_crash_is_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """SRV-D4: a crash is -32603 with no detail, not an isError result."""
    def explode(self, *args, **kwargs):
        raise RuntimeError("secret-detail-41")
    monkeypatch.setattr(World, "acknowledge", explode)
    # Catch inside `async with`: an error that escapes the Client's task group arrives as an ExceptionGroup.
    async with connect(build_server(build_world(), identity=writer()), MODE) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("acknowledge_alert", {"alert_id": "ALR-0001"})
    assert caught.value.error.code == INTERNAL_ERROR
    assert caught.value.error.message == "Internal error"
    assert "secret-detail-41" not in str(caught.value.error)
