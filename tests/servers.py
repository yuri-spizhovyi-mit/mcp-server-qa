"""Helpers for tests that talk to an MCP server. Not a test module (rtm and pytest skip it)."""

from __future__ import annotations

# Client(mode=...): "legacy" negotiates 2025-11-25 with initialize; "2026-07-28" pins the new revision.
MODES = ("legacy", "2026-07-28")
