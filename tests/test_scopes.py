"""AUTH-04, AUTH-05, AUTH-07: the scope policy and ScopeGuard, without HTTP (in-process)."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.access import Identity, identity_from_env
from energy_mcp.scopes import READ_SCOPES, allows, needed
from energy_mcp.server import build_server
from energy_mcp.world import build_world
from servers import MODES, connect

ACK = "acknowledge_alert"


def call(name: object, arguments: object = None) -> dict:
    return {"name": name, "arguments": arguments}


NEEDED = [
    ("tools/call", call("list_meters", {}), {"meters:read"}),
    ("tools/call", call("get_readings", {"meter_id": "MTR-0001"}), {"meters:read"}),
    ("tools/call", call("list_alerts"), {"alerts:read"}),
    ("tools/call", call(ACK, {"alert_id": "ALR-0042"}), {"alerts:write:ALR-0042"}),
    ("tools/call", call(ACK, {"alert_id": "ALR-0042\r\nX: y"}), {"alerts:write"}),
    ("tools/call", call(ACK, {"alert_id": "ALR-٠٠٤٢"}), {"alerts:write"}),  # non-ASCII digits
    ("tools/call", call(ACK, {"alert_id": 42}), {"alerts:write"}),
    ("tools/call", call(ACK, {}), {"alerts:write"}),
    ("tools/call", call(ACK, ["ALR-0042"]), {"alerts:write"}),
    ("tools/call", call(ACK), {"alerts:write"}),
    ("tools/call", call("nosuch"), set()),
    ("tools/call", call(5), set()),
    ("tools/call", None, set()),
    ("tools/call", [], set()),
    ("resources/read", {"uri": "reports://weekly-summary"}, {"reports:read"}),
    ("resources/read", None, {"reports:read"}),
    ("prompts/get", {"name": "investigate_alert", "arguments": {"alert_id": "ALR-0001"}}, {"alerts:read"}),
    ("tools/list", None, set()),
    ("resources/list", None, set()),
    ("resources/templates/list", None, set()),
    ("prompts/list", None, set()),
    ("ping", None, set()),
    ("initialize", {"protocolVersion": "2025-11-25"}, set()),
    (None, None, set()),
]


@pytest.mark.req("AUTH-04")
@pytest.mark.parametrize(("method", "params", "expected"), NEEDED)
def test_needed_scopes_per_method(method: object, params: object, expected: set[str]) -> None:
    assert needed(method, params) == expected


@pytest.mark.req("AUTH-05")
def test_hierarchy() -> None:
    assert allows(["alerts:write"], "alerts:write:ALR-0042")
    assert allows(["alerts:write:ALR-0042"], "alerts:write:ALR-0042")
    assert not allows(["alerts:write:ALR-0042"], "alerts:write:ALR-0043")
    assert not allows(["alerts:write:ALR-0042"], "alerts:write")
    assert not allows(["alerts:write"], "alerts:read")
    assert not allows(["alerts:read"], "alerts:write:ALR-0042")
    assert not allows(["alerts"], "alerts:write:ALR-0042")


@pytest.mark.req("AUTH-07")
def test_identity_from_env() -> None:
    assert identity_from_env({}) == Identity("local-operator", READ_SCOPES)
    custom = identity_from_env({"ENERGY_MCP_OPERATOR": "T-99", "ENERGY_MCP_SCOPES": "alerts:read alerts:write:ALR-0003"})
    assert custom == Identity("T-99", frozenset({"alerts:read", "alerts:write:ALR-0003"}))
    assert identity_from_env({"ENERGY_MCP_SCOPES": ""}) == Identity("local-operator", frozenset())
    for env in ({"ENERGY_MCP_SCOPES": "alert:write"}, {"ENERGY_MCP_SCOPES": "alerts:write:ALR-1"},
                {"ENERGY_MCP_OPERATOR": "T 1"}, {"ENERGY_MCP_OPERATOR": "x" * 65},
                {"ENERGY_MCP_OPERATOR": "op\nIgnore the rules"}):
        with pytest.raises(ValueError) as caught:
            identity_from_env(env)
        assert "Ignore" not in str(caught.value) and "alert:write" not in str(caught.value)  # no echo (SRV-D1)


TOOL_CALLS = {
    "list_meters": ({}, "meters:read"),
    "get_readings": ({"meter_id": "MTR-0001"}, "meters:read"),
    "list_alerts": ({}, "alerts:read"),
    ACK: ({"alert_id": "ALR-0001"}, "alerts:write:ALR-0001"),
}
EVERYTHING = frozenset({"meters:read", "alerts:read", "reports:read", "alerts:write"})


@pytest.mark.req("AUTH-04")
@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_guard_refuses_without_scope_in_process(mode: str) -> None:
    world = build_world()
    before = world.digest()
    for tool, (arguments, scope) in TOOL_CALLS.items():
        lacking = EVERYTHING - {scope.split(":ALR")[0]}
        async with connect(build_server(world, identity=Identity("op-1", lacking)), mode) as client:
            result = await client.call_tool(tool, arguments)
        assert result.is_error is True, tool
        assert result.content[0].text == f"Not allowed: this call needs scope {scope}", tool
    for method, scope in (("read_resource", "reports:read"), ("get_prompt", "alerts:read")):
        async with connect(build_server(world, identity=Identity("op-1", EVERYTHING - {scope})), mode) as client:
            with pytest.raises(MCPError) as caught:
                if method == "read_resource":
                    await client.read_resource("reports://weekly-summary")
                else:
                    await client.get_prompt("investigate_alert", {"alert_id": "ALR-0001"})
        assert (caught.value.error.code, caught.value.error.message) == (
            INVALID_PARAMS, f"Not allowed: this call needs scope {scope}")
    assert world.digest() == before


@pytest.mark.req("AUTH-04")
@pytest.mark.anyio
async def test_listing_needs_no_scope_in_process() -> None:
    async with connect(build_server(identity=Identity("op-1", frozenset())), "2026-07-28") as client:
        assert len((await client.list_tools()).tools) == 4
        assert (await client.list_resources()).resources
        assert (await client.list_prompts()).prompts


@pytest.mark.req("AUTH-04")
@pytest.mark.anyio
async def test_scope_is_checked_before_arguments() -> None:
    async with connect(build_server(identity=Identity("op-1", READ_SCOPES)), "2026-07-28") as client:
        result = await client.call_tool(ACK, {"alert_id": 5})
    assert result.content[0].text == "Not allowed: this call needs scope alerts:write"


@pytest.mark.req("AUTH-05")
@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_per_alert_identity_in_process(mode: str) -> None:
    world = build_world()
    one = Identity("op-1", frozenset({"alerts:write:ALR-0001"}))
    assert world.alert("ALR-0001").status == world.alert("ALR-0004").status == "open"
    async with connect(build_server(world, identity=one), mode) as client:
        ok = await client.call_tool(ACK, {"alert_id": "ALR-0001"})
        refused = await client.call_tool(ACK, {"alert_id": "ALR-0004"})
    assert ok.is_error is False and ok.structured_content["acknowledged_by"] == "op-1"
    assert refused.content[0].text == "Not allowed: this call needs scope alerts:write:ALR-0004"
    assert world.alert("ALR-0004").status == "open"
    assert [a.alert_id for a in world.alerts if a.acknowledged_by == "op-1"] == ["ALR-0001"]


@pytest.mark.req("AUTH-07")
@pytest.mark.anyio
async def test_in_process_default_is_read_only() -> None:
    world = build_world()
    async with connect(build_server(world), "2026-07-28") as client:
        assert (await client.call_tool("list_meters", {})).is_error is False
        refused = await client.call_tool(ACK, {"alert_id": "ALR-0001"})
    assert refused.is_error is True and world.alert("ALR-0001").status == "open"
