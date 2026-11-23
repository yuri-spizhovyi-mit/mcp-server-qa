"""PROTO-09: energy_mcp answers malformed stdio lines (F-13, PROTO-D4) and rejects list cursors (F-14)."""

from __future__ import annotations

import sys

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.server import build_server
from servers import ENVELOPE, MODES, RawStdio, connect

pytestmark = pytest.mark.req("PROTO-09")

ENERGY = [sys.executable, "-m", "energy_mcp"]


@pytest.mark.parametrize(("line", "code", "request_id"), [
    (b'{"jsonrpc":"2.0","id":2,', -32700, None),
    (b'{"jsonrpc":"2.0","id":2}', -32600, 2),
    (b"[1,2]", -32600, None),
    (b'{"jsonrpc":"2.0","id":"x","method":5}', -32600, "x"),
    (b'"\\u00ff"', -32600, None),
], ids=["not-json", "no-method", "array", "method-not-string", "json-string"])
def test_malformed_lines_get_an_answer(line: bytes, code: int, request_id: object) -> None:
    with RawStdio(ENERGY) as wire:
        wire.send(line)
        answer = wire.read()
        wire.send({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {"_meta": ENVELOPE}})
        after = wire.read()
    assert answer is not None, "no answer to a malformed line (F-13)"
    assert (answer["jsonrpc"], answer["id"], answer["error"]["code"]) == ("2.0", request_id, code)
    assert after is not None and after["id"] == 9 and "result" in after


LIST_CALLS = ["list_tools", "list_resources", "list_resource_templates", "list_prompts"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("method", LIST_CALLS)
async def test_list_cursor_is_invalid_params(mode: str, method: str) -> None:
    async with connect(build_server(), mode) as client:
        with pytest.raises(MCPError) as caught:
            await getattr(client, method)(cursor="mcpqa-invalid-cursor")
    assert (caught.value.error.code, caught.value.error.message) == (INVALID_PARAMS, "Invalid cursor")


@pytest.mark.anyio
@pytest.mark.parametrize("method", LIST_CALLS)
async def test_list_without_cursor_still_works(method: str) -> None:
    async with connect(build_server(), "2026-07-28") as client:
        assert await getattr(client, method)() is not None
