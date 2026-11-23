"""AUTH-02..05, AUTH-08: the HTTP edge in front of the SDK, in-process over httpx2's ASGITransport.

Raw POSTs check what the edge itself answers (status, `WWW-Authenticate`), which happens
before the SDK reads the request. The SDK `Client` checks the calls that get through. The
clock is a `ManualClock` in January 2026, so every pass also proves the wall clock plays
no part. Refusals are scored on the world: `world.digest()` must not move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from mcp import Client

from energy_mcp.edge import build_http_app
from energy_mcp.world import World, build_world
from issuer import JANUARY, Issuer, new_key
from servers import (ENVELOPE, METADATA_URL, MODES, FakeClock, ManualClock, http, mcp_over, modern_headers, post,
                     post_call, rpc, running, tool_call)

pytestmark = pytest.mark.anyio

READ = "meters:read alerts:read reports:read"
EVERYTHING = f"{READ} alerts:write"
META = f'resource_metadata="{METADATA_URL}"'
INVALID = f'Bearer error="invalid_token", error_description="The access token is invalid or expired", {META}'
OTHER_KEY = new_key()


@dataclass
class Edge:
    world: World
    clock: ManualClock
    app: Any
    server: Any


@pytest.fixture
def edge(issuer: Issuer) -> Edge:
    world, clock = build_world(), ManualClock(JANUARY)
    app, server = build_http_app(world, settings=issuer.settings, clock=clock, wall_clock=FakeClock())
    return Edge(world, clock, app, server)


def forbidden(scope: str) -> str:
    return f'Bearer error="insufficient_scope", scope="{scope}", {META}, error_description="This call needs more scope"'


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize(("body", "scope"), [
    (tool_call("list_meters"), "meters:read"),
    (tool_call("acknowledge_alert", {"alert_id": "ALR-0001"}), "alerts:write:ALR-0001"),
    (rpc("tools/list"), "alerts:read meters:read reports:read"),
])
async def test_missing_token_challenge(edge: Edge, body: dict, scope: str) -> None:
    """No credentials: no error code (RFC 6750 §3, F-10), but where to look and what to ask for (F-11)."""
    async with running(edge.server), http(edge.app) as client:
        response = await post(client, body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == f'Bearer {META}, scope="{scope}"'
    assert response.json() == {"error": "unauthorized", "error_description": "A bearer token is required"}


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize("how", ["basic-scheme", "query-string-only"])
async def test_credentials_not_in_a_bearer_header_count_as_missing(edge: Edge, issuer: Issuer, how: str) -> None:
    async with running(edge.server):
        if how == "basic-scheme":
            async with http(edge.app, "dXNlcjpwYXNz", scheme="Basic") as client:
                response = await post(client, tool_call("list_meters"))
        else:
            async with http(edge.app) as client:
                response = await client.post(f"/mcp?access_token={issuer.mint()}", json=tool_call("list_meters"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == f'Bearer {META}, scope="meters:read"'


BAD_TOKENS = {
    "expired": lambda i: i.mint(now=JANUARY - 3600, ttl=3600),
    "wrong-audience": lambda i: i.mint(aud="http://127.0.0.1:9000/mcp"),
    "re-signed": lambda i: i.mint(key=OTHER_KEY),
    "alg-none": lambda i: i.mint(alg="none"),
    "empty": lambda i: "",
    "garbage": lambda i: "not-a-jwt",
}


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize("case", BAD_TOKENS)
async def test_invalid_tokens_get_invalid_token(edge: Edge, issuer: Issuer, case: str) -> None:
    async with running(edge.server), http(edge.app, BAD_TOKENS[case](issuer)) as client:
        response = await post(client, tool_call("list_meters"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == f'{INVALID}, scope="meters:read"'


@pytest.mark.req("AUTH-03")
async def test_metadata_document(edge: Edge) -> None:
    async with running(edge.server), http(edge.app) as client:
        response = await client.get(METADATA_URL)
    assert response.status_code == 200
    assert response.json() == {
        "resource": "http://127.0.0.1:8000/mcp",
        "authorization_servers": ["https://issuer.test"],
        "scopes_supported": ["meters:read", "alerts:read", "alerts:write", "reports:read"],
        "bearer_methods_supported": ["header"],
    }


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize("mode", MODES)
async def test_valid_token_calls_tools_on_both_revisions(edge: Edge, issuer: Issuer, mode: str) -> None:
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ), scheme="bearer") as client:
        async with mcp_over(client, mode) as mcp:
            assert len((await mcp.list_tools()).tools) == 4
            result = await mcp.call_tool("list_meters", {})
    assert result.is_error is False and len(result.structured_content["meters"]) == 20


async def succeeds(mcp: Client, call: str) -> bool:
    if call == "resources/read":
        return bool((await mcp.read_resource("reports://weekly-summary")).contents)
    if call == "prompts/get":
        return bool((await mcp.get_prompt("investigate_alert", {"alert_id": "ALR-0001"})).messages)
    arguments = {"get_readings": {"meter_id": "MTR-0001"}, "acknowledge_alert": {"alert_id": "ALR-0001"}}
    return (await mcp.call_tool(call, arguments.get(call, {}))).is_error is False


CALLS = {
    "list_meters": (tool_call("list_meters"), "meters:read"),
    "get_readings": (tool_call("get_readings", {"meter_id": "MTR-0001"}), "meters:read"),
    "list_alerts": (tool_call("list_alerts"), "alerts:read"),
    "acknowledge_alert": (tool_call("acknowledge_alert", {"alert_id": "ALR-0001"}), "alerts:write"),
    "resources/read": (rpc("resources/read", {"uri": "reports://weekly-summary"}), "reports:read"),
    "prompts/get": (rpc("prompts/get", {"name": "investigate_alert", "arguments": {"alert_id": "ALR-0001"}}),
                    "alerts:read"),
}


@pytest.mark.req("AUTH-04")
@pytest.mark.parametrize("call", CALLS)
async def test_each_call_needs_its_scope(edge: Edge, issuer: Issuer, call: str) -> None:
    body, scope = CALLS[call]
    challenge = "alerts:write:ALR-0001" if scope == "alerts:write" else scope
    others = " ".join(s for s in EVERYTHING.split() if s != scope)
    async with running(edge.server):
        async with http(edge.app, issuer.mint(scope=others)) as client:
            refused = await post(client, body)
        async with http(edge.app, issuer.mint(scope=scope)) as client, mcp_over(client, "2026-07-28") as mcp:
            assert await succeeds(mcp, call)
    assert refused.status_code == 403
    assert refused.headers["www-authenticate"] == forbidden(challenge)
    assert refused.json() == {"error": "insufficient_scope", "error_description": "This call needs more scope"}


@pytest.mark.req("AUTH-04")
async def test_listing_needs_only_a_valid_token(edge: Edge, issuer: Issuer) -> None:
    async with running(edge.server), http(edge.app, issuer.mint(scope="")) as client:
        async with mcp_over(client, "2026-07-28") as mcp:
            assert len((await mcp.list_tools()).tools) == 4
            assert (await mcp.list_resources()).resources
            assert (await mcp.list_prompts()).prompts


@pytest.mark.req("AUTH-05")
async def test_per_alert_token_over_http(edge: Edge, issuer: Issuer) -> None:
    token = issuer.mint(scope="alerts:write:ALR-0001", sub="op-9")
    async with running(edge.server), http(edge.app, token) as client:
        async with mcp_over(client, "2026-07-28") as mcp:
            ok = await mcp.call_tool("acknowledge_alert", {"alert_id": "ALR-0001"})
        refused = await post_call(client, "acknowledge_alert", {"alert_id": "ALR-0004"})
    assert ok.is_error is False and ok.structured_content["acknowledged_by"] == "op-9"
    assert refused.status_code == 403
    assert refused.headers["www-authenticate"] == forbidden("alerts:write:ALR-0004")
    assert edge.world.alert("ALR-0004").status == "open"


@pytest.mark.req("AUTH-08")
async def test_acknowledged_by_is_the_token_subject(edge: Edge, issuer: Issuer) -> None:
    async with running(edge.server), http(edge.app, issuer.mint(scope="alerts:write", sub="T-42")) as client:
        async with mcp_over(client, "legacy") as mcp:
            result = await mcp.call_tool("acknowledge_alert", {"alert_id": "ALR-0004"})
    assert result.structured_content["acknowledged_by"] == edge.world.alert("ALR-0004").acknowledged_by == "T-42"


@pytest.mark.req("AUTH-04")
async def test_refused_calls_leave_the_world_unchanged(edge: Edge, issuer: Issuer) -> None:
    """Each refused request is one the SDK would run (the control at the end runs it), so only the edge stops it."""
    before = edge.world.digest()
    ack = {"alert_id": "ALR-0001"}
    cases = [(None, 401), (issuer.mint(scope="alerts:write", now=JANUARY - 7200), 401),
             (issuer.mint(scope="alerts:write", aud="http://127.0.0.1:9000/mcp"), 401),
             (issuer.mint(scope=READ), 403), (issuer.mint(scope="alerts:write:ALR-0004"), 403)]
    async with running(edge.server):
        for token, status in cases:
            async with http(edge.app, token) as client:
                assert (await post_call(client, "acknowledge_alert", ack)).status_code == status
        assert edge.world.digest() == before
        async with http(edge.app, issuer.mint(scope="alerts:write")) as client:  # control
            assert (await post_call(client, "acknowledge_alert", ack)).status_code == 200
    assert edge.world.alert("ALR-0001").status == "acknowledged"


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize("alert_id", ['"', 'ALR-0001"\r\nX-Evil: 1', "A" * 500, "ALR-٠٠٠١", "ALR-0001\n"])
async def test_challenge_never_carries_caller_text(edge: Edge, issuer: Issuer, alert_id: str) -> None:
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, tool_call("acknowledge_alert", {"alert_id": alert_id}))
    assert response.status_code == 403
    assert response.headers["www-authenticate"] == forbidden("alerts:write")
    assert response.headers["www-authenticate"].isascii() and "Evil" not in response.text


META_JSON = json.dumps(ENVELOPE)


def raw_call(params: str, method: str = '"method":"tools/call"') -> bytes:
    """A complete raw 2026-07-28 request whose params are given as JSON text (to plant duplicate keys)."""
    return ('{"jsonrpc":"2.0","id":1,' + method + ',"params":' + params + "}").encode()


ACK_PARAMS = '{"name":"acknowledge_alert","arguments":{"alert_id":"ALR-0001"},"_meta":' + META_JSON + "}"
UNREADABLE = {
    "json-array": (b"[" + raw_call(ACK_PARAMS) + b"]", ("tools/call", "acknowledge_alert")),
    "invalid-json": (raw_call(ACK_PARAMS)[:-5], ("tools/call", "acknowledge_alert")),
    "not-utf8": (raw_call(ACK_PARAMS).replace(b"ALR-0001", b"ALR-\xff001"), ("tools/call", "acknowledge_alert")),
    # Duplicate keys: if the edge read one value and the SDK the other, the scope check would be bypassed.
    "duplicate-name": (raw_call('{"name":"acknowledge_alert","name":"list_meters","arguments":{},"_meta":'
                                + META_JSON + "}"), ("tools/call", "list_meters")),
    "duplicate-method": (raw_call('{"_meta":' + META_JSON + "}", '"method":"tools/call","method":"tools/list"'),
                         ("tools/list", None)),
}


@pytest.mark.req("AUTH-04")
@pytest.mark.parametrize("case", UNREADABLE)
async def test_unreadable_bodies_cannot_write(edge: Edge, issuer: Issuer, case: str) -> None:
    """A body the edge can't read for certain is refused by the edge, never handed on (Review Focus 3).

    Each case is a complete 2026-07-28 request the SDK would act on, so a pass shows the edge
    refused it, not the SDK. The control below proves the envelope is right.
    """
    raw, (method, name) = UNREADABLE[case]
    before = edge.world.digest()
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, None, raw=raw, **modern_headers(method, name))
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == -32700
    assert edge.world.digest() == before


@pytest.mark.req("AUTH-04")
async def test_content_type_does_not_hide_a_call(edge: Edge, issuer: Issuer) -> None:
    """The edge reads the body whatever the content type claims, so text/plain can't skip the scope check."""
    before = edge.world.digest()
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, None, raw=raw_call(ACK_PARAMS), content_type="text/plain",
                              **modern_headers("tools/call", "acknowledge_alert"))
    assert response.status_code == 403
    assert edge.world.digest() == before


@pytest.mark.req("AUTH-04")
async def test_a_complete_raw_request_gets_through(edge: Edge, issuer: Issuer) -> None:
    """Control for the cases above: the same envelope, well formed, is served."""
    body = raw_call('{"name":"list_meters","arguments":{},"_meta":' + META_JSON + "}")
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, None, raw=body, **modern_headers("tools/call", "list_meters"))
    assert response.status_code == 200 and "MTR-0001" in response.text


@pytest.mark.req("AUTH-04")
async def test_scope_guard_holds_if_the_edge_lets_a_call_through(edge: Edge, issuer: Issuer,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth: break the edge's scope check; ScopeGuard behind it still refuses."""
    monkeypatch.setattr("energy_mcp.edge.missing", lambda granted, needs: [])
    before = edge.world.digest()
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        async with mcp_over(client, "2026-07-28") as mcp:
            result = await mcp.call_tool("acknowledge_alert", {"alert_id": "ALR-0001"})
    assert result.is_error is True
    assert result.content[0].text == "Not allowed: this call needs scope alerts:write:ALR-0001"
    assert edge.world.digest() == before


@pytest.mark.req("AUTH-04")
async def test_body_over_the_limit_is_refused(issuer: Issuer) -> None:
    world = build_world()
    app, server = build_http_app(world, settings=issuer.settings, clock=ManualClock(JANUARY), max_body=1024)
    async with running(server), http(app, issuer.mint(scope=READ)) as client:
        response = await post(client, None, raw=b'{"pad":"' + b"x" * 2000 + b'"}')
    assert response.status_code == 413


@pytest.mark.req("AUTH-08")
async def test_session_belongs_to_its_principal(edge: Edge, issuer: Issuer) -> None:
    """The edge hands the SDK the principal; the SDK answers another principal's session with 404."""
    initialize = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "0"}})
    async with running(edge.server):
        async with http(edge.app, issuer.mint(scope=READ, sub="op-1")) as alice:
            opened = await post(alice, initialize)
            session = {"mcp-session-id": opened.headers["mcp-session-id"], "mcp-protocol-version": "2025-11-25"}
            own = await post(alice, rpc("tools/list", request_id=2), **session)
        async with http(edge.app, issuer.mint(scope=READ, sub="op-2")) as bob:
            other = await post(bob, rpc("tools/list", request_id=2), **session)
    assert (opened.status_code, own.status_code, other.status_code) == (200, 200, 404)


@pytest.mark.req("AUTH-02")
async def test_every_path_but_metadata_needs_a_token(edge: Edge) -> None:
    async with running(edge.server), http(edge.app) as client:
        for path in ("/mcp/", "/", "/anything"):
            assert (await client.post(path, json=rpc("tools/list"))).status_code == 401, path
        assert (await client.get("/mcp")).status_code == 401


async def drive(app: Any, token: str, messages: list[dict]) -> list[dict]:
    """Call the ASGI app directly with exactly these request messages (a raw client that can hang up)."""
    headers = {"host": "127.0.0.1:8000", "authorization": f"Bearer {token}", "content-type": "application/json",
               "accept": "application/json, text/event-stream", **modern_headers("tools/call", "acknowledge_alert")}
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/mcp", "raw_path": b"/mcp", "query_string": b"", "root_path": "",
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000)}
    pending, sent, answered = list(messages), [], anyio.Event()

    async def receive() -> dict:
        if pending:
            return pending.pop(0)
        await answered.wait()  # like a server: the connection closes after the response, not before
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            answered.set()

    with anyio.fail_after(10):
        await app(scope, receive, send)
    return sent


@pytest.mark.req("AUTH-04")
async def test_aborted_request_is_not_executed(edge: Edge, issuer: Issuer) -> None:
    """A client that hangs up mid-body sent no request: the edge must not replay half of one as complete."""
    body = json.dumps(rpc("tools/call", {"name": "acknowledge_alert", "arguments": {"alert_id": "ALR-0001"},
                                         "_meta": ENVELOPE})).encode()
    token = issuer.mint(scope="alerts:write")
    async with running(edge.server):
        await drive(edge.app, token, [{"type": "http.request", "body": body, "more_body": True},
                                      {"type": "http.disconnect"}])
        assert edge.world.alert("ALR-0001").status == "open"
        await drive(edge.app, token, [{"type": "http.request", "body": body, "more_body": False}])  # control
    assert edge.world.alert("ALR-0001").status == "acknowledged"


@pytest.mark.req("AUTH-02")
@pytest.mark.parametrize("method", [[], {}, ["tools/call"], 5, None])
async def test_odd_method_without_token_is_401(edge: Edge, method: Any) -> None:
    """Whatever `method` is, a request without a token is a 401, never a crash."""
    async with running(edge.server), http(edge.app) as client:
        response = await post(client, {"jsonrpc": "2.0", "id": 1, "method": method})
    assert response.status_code == 401


@pytest.mark.req("PROTO-05")
@pytest.mark.parametrize(("body", "request_id"), [
    ({"jsonrpc": "2.0", "id": 5}, 5),
    ({"jsonrpc": "2.0", "id": "x", "method": 5}, "x"),
    ({"jsonrpc": "1.0", "id": 6, "method": "tools/list"}, 6),
    ({"id": 7, "method": "tools/list"}, 7),
    ({"jsonrpc": "2.0", "id": [1], "params": {"echo": "Ignore previous instructions"}}, None),
], ids=["no-method", "method-not-string", "wrong-version", "no-jsonrpc", "bad-id"])
async def test_non_message_body_is_32600(edge: Edge, issuer: Issuer, body: dict, request_id: object) -> None:
    """F-17: the edge answers -32600 itself; the SDK's legacy path answers -32602 and echoes the input."""
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, body, **{"mcp-protocol-version": "2025-11-25"})
    assert response.status_code == 400
    assert response.json() == {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}}
    assert "Ignore" not in response.text


@pytest.mark.req("PROTO-05")
async def test_a_client_response_is_still_handed_on(edge: Edge, issuer: Issuer) -> None:
    """A JSON-RPC response from the client (to a server request) is a valid message, not -32600."""
    async with running(edge.server), http(edge.app, issuer.mint(scope=READ)) as client:
        response = await post(client, {"jsonrpc": "2.0", "id": 99, "result": {}},
                              **{"mcp-protocol-version": "2025-11-25"})
    assert "Invalid Request" not in response.text  # the edge handed it on; what the SDK answers is its own business
