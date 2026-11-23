"""Helpers for tests that talk to an MCP server. Not a test module (rtm and pytest skip it)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema
from mcp import Client

# Client(mode=...): "legacy" negotiates 2025-11-25 with initialize; "2026-07-28" pins the new revision.
MODES = ("legacy", "2026-07-28")


def connect(server: Any, mode: str) -> Client:
    return Client(server, mode=mode)


def validate(instance: Any, schema: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError unless instance matches schema (Draft 2020-12)."""
    jsonschema.Draft202012Validator(schema).validate(instance)


class FakeClock:
    """A clock for acknowledge_alert: 2026-01-12 09:00 UTC, one minute later on each call."""

    def __init__(self, start: datetime = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        now, self.now = self.now, self.now + timedelta(minutes=1)
        return now
