"""Findings F-09..F-12: what the SDK's own resource-server auth (mcp 2.3.0) answers.

Characterization tests, like `test_sdk_findings.py`: they pin the SDK's current behaviour on
a bare `MCPServer` with a toy token verifier, so they pass on the SDK we use. `energy_mcp`
does not use these SDK pieces; its own edge (`energy_mcp/edge.py`) answers instead. If an
SDK upgrade changes one of them, its pin fails and the finding in `docs/findings.md` is
revisited. They never start `energy_mcp`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

pytestmark = pytest.mark.anyio

BASE = "http://127.0.0.1:8000"


class ToyVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token == "good":
            return AccessToken(token=token, client_id="c", scopes=["meters:read"])
        if token == "wallclock-expired":  # 2026-01-05: valid on a January fake clock, past on the wall clock
            return AccessToken(token=token, client_id="c", scopes=["meters:read"], expires_at=1767600000)
        return None


@asynccontextmanager
async def sdk_auth_app(required_scopes: list[str] | None) -> AsyncIterator[httpx2.AsyncClient]:
    server = MCPServer("bare", token_verifier=ToyVerifier(), auth=AuthSettings(
        issuer_url="https://issuer.test", resource_server_url=f"{BASE}/mcp",
        required_scopes=required_scopes, validate_token_resource=False))
    app = server.streamable_http_app(json_response=True)
    async with server.session_manager.run():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as client:
            yield client


async def post(client: httpx2.AsyncClient, token: str | None) -> httpx2.Response:
    headers = {"content-type": "application/json"} | ({"authorization": f"Bearer {token}"} if token else {})
    return await client.post("/mcp", content="{}", headers=headers)


@pytest.mark.req("AUTH-01")
async def test_f09_sdk_checks_expiry_on_the_wall_clock() -> None:
    async with sdk_auth_app(None) as client:
        assert (await post(client, "good")).status_code != 401
        assert (await post(client, "wallclock-expired")).status_code == 401


@pytest.mark.req("AUTH-02")
async def test_f10_sdk_missing_token_says_invalid_token() -> None:
    async with sdk_auth_app(None) as client:
        missing, bad = await post(client, None), await post(client, "bad")
    assert missing.status_code == bad.status_code == 401
    assert 'error="invalid_token"' in missing.headers["www-authenticate"]
    assert missing.headers["www-authenticate"] == bad.headers["www-authenticate"]


@pytest.mark.req("AUTH-02")
async def test_f11_sdk_challenges_carry_no_scope() -> None:
    async with sdk_auth_app(["meters:read", "alerts:write"]) as client:
        unauthorized, forbidden = await post(client, None), await post(client, "good")
    assert (unauthorized.status_code, forbidden.status_code) == (401, 403)
    assert 'error="insufficient_scope"' in forbidden.headers["www-authenticate"]
    for response in (unauthorized, forbidden):
        assert "scope=" not in response.headers["www-authenticate"].replace("insufficient_scope", "")


@pytest.mark.req("AUTH-03")
async def test_f12_sdk_metadata_scopes_are_the_required_scopes() -> None:
    async with sdk_auth_app(None) as client:
        bare = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    async with sdk_auth_app(["meters:read"]) as client:
        required = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert "scopes_supported" not in bare
    assert required["scopes_supported"] == ["meters:read"]
