"""SRV-06: the investigate_alert prompt, and -32602 for every bad prompts/get (F-03)."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.server import build_server
from energy_mcp.world import build_world
from servers import MODES, connect

pytestmark = [pytest.mark.req("SRV-06"), pytest.mark.anyio, pytest.mark.parametrize("mode", MODES)]


async def test_prompt_is_listed_with_its_required_argument(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        prompts = {p.name: p for p in (await client.list_prompts()).prompts}
    assert set(prompts) == {"investigate_alert"}
    arguments = prompts["investigate_alert"].arguments
    assert [(a.name, a.required) for a in arguments] == [("alert_id", True)]


async def test_prompt_renders_the_alert(mode: str) -> None:
    alert = build_world().alerts[6]
    async with connect(build_server(), mode) as client:
        result = await client.get_prompt("investigate_alert", {"alert_id": alert.alert_id})
    assert len(result.messages) == 1 and result.messages[0].role == "user"
    text = result.messages[0].content.text
    for expected in (alert.alert_id, alert.meter_id, alert.severity, "get_readings", "acknowledge_alert"):
        assert expected in text, expected


@pytest.mark.parametrize(("name", "args", "message"), [
    ("nosuch", {"alert_id": "ALR-0001"}, "Unknown prompt; available: investigate_alert"),
    ("investigate_alert", {}, "Missing required argument for prompt investigate_alert: alert_id"),
    ("investigate_alert", {"alert_id": "ALR-0001", "x": "1"},
     "Unexpected argument for prompt investigate_alert; allowed: alert_id"),
    ("investigate_alert", {"alert_id": "ALR-0999"}, "Unknown alert: ALR-0999"),
    ("investigate_alert", {"alert_id": "nope"}, "alert_id must look like ALR-0001"),
    ("investigate_alert", {"alert_id": "ALR-0001\n"}, "alert_id must look like ALR-0001"),
    ("SENTINEL-7Q", {}, "Unknown prompt; available: investigate_alert"),
    ("investigate_alert", {"alert_id": "ALR-0001", "SENTINEL-7Q": "1"},
     "Unexpected argument for prompt investigate_alert; allowed: alert_id"),
], ids=["unknown-prompt", "missing", "unexpected", "unknown-alert", "malformed-id", "trailing-newline",
        "sentinel-name", "sentinel-argument"])
async def test_prompt_errors_are_invalid_params(mode: str, name: str, args: dict, message: str) -> None:
    async with connect(build_server(), mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.get_prompt(name, args)
    assert (caught.value.error.code, caught.value.error.message) == (INVALID_PARAMS, message)
