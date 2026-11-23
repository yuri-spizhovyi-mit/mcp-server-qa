"""AUTH-06: a fixed-window rate limit per principal (AUTH-D2), on a fake clock."""

from __future__ import annotations

import pytest

from energy_mcp.edge import build_http_app
from energy_mcp.ratelimit import RateLimiter
from energy_mcp.world import build_world
from issuer import JANUARY, Issuer
from servers import FakeClock, ManualClock, http, post, post_call, running, tool_call

pytestmark = pytest.mark.req("AUTH-06")

READ = "meters:read alerts:read reports:read"
LIST = tool_call("list_meters")


def limited_app(issuer: Issuer, clock: ManualClock):
    world = build_world()
    app, server = build_http_app(world, settings=issuer.settings, clock=clock, wall_clock=FakeClock(), rate_limit=3,
                                 rate_window=60)
    return world, app, server


def test_limiter_counts_and_resets() -> None:
    clock = ManualClock(JANUARY)
    limiter = RateLimiter(limit=3, window=60, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == 60.0
    clock.advance(59.5)
    assert limiter.hit("a") == 0.5
    assert limiter.hit("b") is None
    clock.advance(0.5)
    assert limiter.hit("a") is None


@pytest.mark.anyio
async def test_call_n_plus_one_is_429_and_resets_on_the_clock(issuer: Issuer) -> None:
    clock = ManualClock(JANUARY)
    _, app, server = limited_app(issuer, clock)
    async with running(server), http(app, issuer.mint(scope=READ)) as client:
        assert [(await post(client, LIST)).status_code == 429 for _ in range(3)] == [False] * 3
        limited = await post(client, LIST)
        clock.advance(59)
        still = await post(client, LIST)
        clock.advance(1)
        again = await post(client, LIST)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json() == {"error": "rate_limited", "error_description": "Too many requests"}
    assert (still.status_code, still.headers["retry-after"]) == (429, "1")
    assert again.status_code != 429


@pytest.mark.anyio
async def test_new_token_same_principal_stays_limited(issuer: Issuer) -> None:
    _, app, server = limited_app(issuer, ManualClock(JANUARY))
    async with running(server):
        async with http(app, issuer.mint(scope=READ)) as client:
            for _ in range(3):
                await post(client, LIST)
        async with http(app, issuer.mint(scope=READ)) as fresh:  # a new jti, same iss/sub/client_id
            assert (await post(fresh, LIST)).status_code == 429


@pytest.mark.anyio
async def test_other_principal_unaffected(issuer: Issuer) -> None:
    _, app, server = limited_app(issuer, ManualClock(JANUARY))
    async with running(server):
        async with http(app, issuer.mint(scope=READ, sub="op-1")) as one:
            for _ in range(4):
                await post(one, LIST)
        async with http(app, issuer.mint(scope=READ, sub="op-2")) as two:
            assert (await post(two, LIST)).status_code != 429
        async with http(app, issuer.mint(scope=READ, sub="op-1", client_id="agent-2")) as other_client:
            assert (await post(other_client, LIST)).status_code != 429


@pytest.mark.anyio
async def test_refused_scope_calls_count(issuer: Issuer) -> None:
    _, app, server = limited_app(issuer, ManualClock(JANUARY))
    async with running(server), http(app, issuer.mint(scope="alerts:read")) as client:
        statuses = [(await post(client, LIST)).status_code for _ in range(4)]
    assert statuses == [403, 403, 403, 429]


@pytest.mark.anyio
async def test_limited_call_never_reaches_the_server(issuer: Issuer) -> None:
    """ScopeGuard allows this call, so only the edge's order (429 before the SDK) protects the world."""
    clock = ManualClock(JANUARY)
    world, app, server = limited_app(issuer, clock)
    before = world.digest()
    async with running(server), http(app, issuer.mint(scope=f"{READ} alerts:write")) as client:
        for _ in range(3):
            await post(client, LIST)
        limited = await post_call(client, "acknowledge_alert", {"alert_id": "ALR-0001"})
        assert limited.status_code == 429
        assert world.digest() == before
        clock.advance(60)  # control: the same request, once the window resets, does change the world
        assert (await post_call(client, "acknowledge_alert", {"alert_id": "ALR-0001"})).status_code == 200
    assert world.alert("ALR-0001").status == "acknowledged"


@pytest.mark.anyio
async def test_unauthenticated_requests_are_not_counted(issuer: Issuer) -> None:
    """AUTH-D2: a 401 is not counted, so nobody can use up someone else's window without their token."""
    _, app, server = limited_app(issuer, ManualClock(JANUARY))
    async with running(server):
        async with http(app) as anonymous:
            assert {(await post(anonymous, LIST)).status_code for _ in range(5)} == {401}
        async with http(app, issuer.mint(scope=READ)) as client:
            assert (await post(client, LIST)).status_code != 429
