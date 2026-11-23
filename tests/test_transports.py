"""SRV-07: stdio and Streamable HTTP, on both revisions, behave like the in-process server."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from mcp.server import MCPServer

from energy_mcp.__main__ import main, parse_args
from energy_mcp.server import TOOL_NAMES
from issuer import Issuer
from servers import MODES, free_port, http_server, stdio_params, validate

pytestmark = pytest.mark.req("SRV-07")


def test_cli_defaults_to_stdio_and_localhost() -> None:
    """Started with no flags, the server must not listen on every interface."""
    args = parse_args([])
    assert (args.transport, args.host, args.port, args.seed) == ("stdio", "127.0.0.1", 8000, 42)


def test_cli_serves_http_on_localhost_unless_told(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """What main() hands uvicorn and the SDK, not just what argparse parsed."""
    key = tmp_path / "issuer.pem"
    key.write_bytes(Issuer().public_pem)
    auth = ["--public-key", str(key), "--issuer", "https://issuer.test"]
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(("http", kw["host"], kw["port"])))
    monkeypatch.setattr(MCPServer, "run", lambda self, transport="stdio", **kw: calls.append((transport, kw)))
    main(["--transport", "http", *auth])
    main(["--transport", "http", "--host", "0.0.0.0", "--port", "9001", *auth])
    main([])
    assert calls == [("http", "127.0.0.1", 8000), ("http", "0.0.0.0", 9001), ("stdio", {})]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_transport_behaves_like_in_process(transport: str, mode: str, tmp_path: Path) -> None:
    with contextlib.ExitStack() as stack:
        if transport == "stdio":
            target = stdio_params()
        else:
            url, token = stack.enter_context(authenticated_http_server(tmp_path))
            client = httpx2.AsyncClient(headers={"authorization": f"Bearer {token}"})
            target = streamable_http_client(url, http_client=client)
        async with Client(target, mode=mode) as client:
            assert client.protocol_version == ("2025-11-25" if mode == "legacy" else mode)
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == set(TOOL_NAMES)
            ok = await client.call_tool("list_meters", {})
            assert ok.is_error is False and len(ok.structured_content["meters"]) == 20
            validate(ok.structured_content, tools["list_meters"].output_schema)
            bad = await client.call_tool("get_readings", {"meter_id": "MTR-0001", "limit": "2"})
            assert bad.is_error is True and 'limit: must satisfy type "integer"' in bad.content[0].text
            with pytest.raises(MCPError) as caught:
                await client.call_tool("nosuch", {})
            assert caught.value.error.code == INVALID_PARAMS


@contextlib.contextmanager
def authenticated_http_server(tmp_path: Path):
    """A real HTTP server process with its own issuer key; yields its URL and a wall-clock read token."""
    port = free_port()
    issuer = Issuer(audience=f"http://127.0.0.1:{port}/mcp")
    key = tmp_path / "issuer.pem"
    key.write_bytes(issuer.public_pem)
    with http_server(port, public_key=key, issuer=issuer.issuer) as url:
        yield url, issuer.mint(scope="meters:read alerts:read reports:read", now=time.time())


@pytest.mark.req("AUTH-09")
@pytest.mark.anyio
async def test_http_process_refuses_without_token(tmp_path: Path) -> None:
    with authenticated_http_server(tmp_path) as (url, _):
        async with httpx2.AsyncClient() as client:
            response = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    metadata = url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
    assert f'resource_metadata="{metadata}"' in response.headers["www-authenticate"]
