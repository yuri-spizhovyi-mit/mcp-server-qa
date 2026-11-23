"""Findings F-13, F-14: where a bare MCPServer (mcp 2.3.0) departs from the spec's protocol rules.

Characterization pins, like `test_sdk_findings.py`: they pass on the SDK we use, and
`energy_mcp` works around each (`energy_mcp/stdio.py`, `energy_mcp/guard.py`). If an SDK
upgrade changes one, its pin fails and the finding is revisited. They never start `energy_mcp`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client

from bare_stdio_server import server as bare
from servers import ENVELOPE, RawStdio

pytestmark = pytest.mark.req("PROTO-09")

BARE = [sys.executable, str(Path(__file__).with_name("bare_stdio_server.py"))]


def test_f13_bare_stdio_drops_malformed_lines() -> None:
    with RawStdio(BARE) as wire:
        wire.send(b'{"jsonrpc":"2.0","id":1,')
        wire.send({"jsonrpc": "2.0", "id": 2})
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": ENVELOPE}})
        first = wire.read()
    assert first is not None and first["id"] == 3  # nothing came back for ids 1 and 2


@pytest.mark.anyio
async def test_f14_bare_list_methods_ignore_a_cursor() -> None:
    async with Client(bare, mode="2026-07-28") as client:
        result = await client.list_tools(cursor="mcpqa-invalid-cursor")
    assert [t.name for t in result.tools] == ["count"]
