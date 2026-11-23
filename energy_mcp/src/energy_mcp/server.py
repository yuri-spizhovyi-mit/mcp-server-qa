"""The energy_mcp server: four tools over the seeded world (resource and prompt: Task 5).

Tools are built as `Tool` objects rather than with `@server.tool()`, so each published
`inputSchema` can say `additionalProperties: false` (SRV-D2) before the server sees it.
"""

from __future__ import annotations

import base64
import binascii
import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.shared.exceptions import MCPError
from mcp_types import ToolAnnotations
from mcp_types.jsonrpc import INTERNAL_ERROR

from energy_mcp import __version__
from energy_mcp.models import Alert, AlertList, MeterList, ReadingPage
from energy_mcp.world import TIME_FORMAT, World, build_world

logger = logging.getLogger("energy_mcp")

TOOL_NAMES = ("list_meters", "get_readings", "list_alerts", "acknowledge_alert")
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ACKNOWLEDGE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _crash_is_internal_error(fn: Callable[..., Any]) -> Callable[..., Any]:
    """SRV-D4: an unexpected exception becomes -32603 "Internal error"; the detail stays in the log."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ToolError, MCPError):
            raise
        except Exception:
            logger.exception("tool %s crashed", fn.__name__)
            raise MCPError(code=INTERNAL_ERROR, message="Internal error") from None

    return wrapper


def _cursor(meter_id: str, offset: int) -> str:
    return base64.urlsafe_b64encode(f"{meter_id}:{offset}".encode()).rstrip(b"=").decode()


def _offset(cursor: str, meter_id: str, size: int) -> int:
    """The offset a cursor from this server encodes for this meter, else ToolError("Invalid cursor")."""
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
        owner, _, raw = text.rpartition(":")
        offset = int(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ToolError("Invalid cursor") from None
    if owner != meter_id or not 0 < offset < size:
        raise ToolError("Invalid cursor")
    return offset


def _tools(world: World, clock: Callable[[], datetime], operator: str) -> list[Tool]:
    def list_meters(site_id: str | None = None) -> MeterList:
        return MeterList(meters=[m for m in world.meters if site_id is None or m.site_id == site_id])

    def get_readings(meter_id: str, limit: int = 24, cursor: str | None = None) -> ReadingPage:
        series = world.readings.get(meter_id)
        if series is None:
            raise ToolError(f"Unknown meter {meter_id}")
        start = 0 if cursor is None else _offset(cursor, meter_id, len(series))
        end = start + limit
        next_cursor = _cursor(meter_id, end) if end < len(series) else None
        return ReadingPage(meter_id=meter_id, readings=series[start:end], next_cursor=next_cursor)

    def list_alerts(status: Literal["open", "acknowledged"] | None = None, meter_id: str | None = None) -> AlertList:
        return AlertList(alerts=[
            a for a in world.alerts
            if (status is None or a.status == status) and (meter_id is None or a.meter_id == meter_id)
        ])

    def acknowledge_alert(alert_id: str) -> Alert:
        if world.alert(alert_id) is None:
            raise ToolError(f"Unknown alert {alert_id}")
        return world.acknowledge(alert_id, by=operator, at=clock().strftime(TIME_FORMAT))

    # Argument patterns and ranges live in the schemas (Task 4 publishes and enforces them).
    specs = [
        (list_meters, READ_ONLY,
         "List the electricity meters, optionally only those at one site (site_id like SITE-01)."),
        (get_readings, READ_ONLY,
         "Hourly energy readings in kWh for one meter (meter_id like MTR-0001), oldest first, "
         "up to `limit` per page; pass `next_cursor` back as `cursor` for the next page."),
        (list_alerts, READ_ONLY,
         "List meter alerts with their severity, status and technician note, optionally filtered by "
         "status (open or acknowledged) and meter_id."),
        (acknowledge_alert, ACKNOWLEDGE,
         "Acknowledge one alert (alert_id like ALR-0001): marks it acknowledged and records who and when. "
         "Acknowledging it again changes nothing."),
    ]
    tools = []
    for fn, annotations, description in specs:
        tool = Tool.from_function(_crash_is_internal_error(fn), name=fn.__name__, description=description,
                                  annotations=annotations)
        tool.parameters = {**tool.parameters, "additionalProperties": False}  # SRV-D2
        tools.append(tool)
    return tools


def build_server(world: World | None = None, *, clock: Callable[[], datetime] = utc_now,
                 operator: str = "local-operator") -> MCPServer:
    world = build_world() if world is None else world
    tools = _tools(world, clock, operator)
    assert tuple(t.name for t in tools) == TOOL_NAMES
    return MCPServer("energy-mcp", version=__version__, tools=tools)
