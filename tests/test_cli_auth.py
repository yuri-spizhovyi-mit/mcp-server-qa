"""AUTH-07, AUTH-09: the CLI's auth flags (AUTH-D6) and the stdio identity from the environment (AUTH-D3)."""

from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from mcp import Client
from mcp.server import MCPServer

from energy_mcp.__main__ import main, parse_args
from energy_mcp.edge import AuthEdge
from issuer import Issuer
from servers import stdio_params


@pytest.mark.req("AUTH-09")
@pytest.mark.parametrize("extra", [[], ["--public-key", "key.pem"], ["--issuer", "https://issuer.test"]])
def test_http_needs_key_and_issuer(extra: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_args(["--transport", "http", *extra])
    assert caught.value.code == 2
    assert "HTTP needs --public-key and --issuer" in capsys.readouterr().err


@pytest.mark.req("AUTH-09")
def test_cli_hands_uvicorn_the_edge(issuer: Issuer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "issuer.pem"
    key.write_bytes(issuer.public_pem)
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    main(["--transport", "http", "--public-key", str(key), "--issuer", issuer.issuer])
    main(["--transport", "http", "--public-key", str(key), "--issuer", issuer.issuer, "--host", "0.0.0.0",
          "--port", "9001", "--resource", "https://energy.test/mcp"])
    (first, first_kw), (second, second_kw) = runs
    assert isinstance(first, AuthEdge) and isinstance(second, AuthEdge)
    assert (first_kw["host"], first_kw["port"], first.resource_url, first.issuer) == (
        "127.0.0.1", 8000, "http://127.0.0.1:8000/mcp", "https://issuer.test")
    assert (second_kw["host"], second_kw["port"], second.resource_url) == ("0.0.0.0", 9001, "https://energy.test/mcp")
    assert first.limiter.limit == 60 and first.limiter.window == 60


@pytest.mark.req("AUTH-07")
def test_bad_env_identity_stops_stdio(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(MCPServer, "run", lambda self, *a, **kw: pytest.fail("the server must not start"))
    monkeypatch.setenv("ENERGY_MCP_SCOPES", "alert:write")
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
    assert "ENERGY_MCP_SCOPES has an unknown scope" in capsys.readouterr().err


@pytest.mark.req("AUTH-07")
@pytest.mark.anyio
async def test_stdio_process_is_read_only_by_default() -> None:
    async with Client(stdio_params(), mode="2026-07-28") as client:
        assert (await client.call_tool("list_meters", {})).is_error is False
        refused = await client.call_tool("acknowledge_alert", {"alert_id": "ALR-0001"})
    assert refused.is_error is True
    assert refused.content[0].text == "Not allowed: this call needs scope alerts:write:ALR-0001"


@pytest.mark.req("AUTH-07")
@pytest.mark.anyio
async def test_stdio_process_with_write_scope_records_operator() -> None:
    env = {"ENERGY_MCP_SCOPES": "alerts:read alerts:write", "ENERGY_MCP_OPERATOR": "T-99"}
    async with Client(stdio_params(env=env), mode="2026-07-28") as client:
        result = await client.call_tool("acknowledge_alert", {"alert_id": "ALR-0001"})
    assert result.is_error is False and result.structured_content["acknowledged_by"] == "T-99"
