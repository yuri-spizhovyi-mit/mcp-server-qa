"""PROTO-01: mcpqa's core: target strings, raw wires, protocol sessions and the pytest plugin."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx2
import pytest

from mcpqa.protocol import LEGACY, MODERN, Session
from mcpqa.target import Target, parse_target
from mcpqa.wire import HttpWire, StdioWire, parse_sse
from servers import authenticated_http_server

pytestmark = pytest.mark.req("PROTO-01")

ENERGY = [sys.executable, "-m", "energy_mcp"]


def test_parse_target_forms() -> None:
    exe = r"C:\Program Files\Python 3.12\python.exe"
    assert parse_target("stdio:" + json.dumps([exe, "-m", "srv"])) == Target("stdio", (exe, "-m", "srv"))
    assert parse_target("stdio:python -m 'my srv'") == Target("stdio", ("python", "-m", "my srv"))
    assert parse_target("http://127.0.0.1:8000/mcp", token_env="T", env={"T": "abc"}) == Target(
        "http", url="http://127.0.0.1:8000/mcp", token="abc")
    for bad in ("", "ftp://x/mcp", "stdio:", "stdio:   ", "stdio:[1]", "stdio:[]", "stdio:[\"a\"", "http://"):
        with pytest.raises(ValueError, match="target"):
            parse_target(bad)
    with pytest.raises(ValueError) as caught:
        parse_target("http://127.0.0.1:8000/mcp", token_env="MCPQA_UNSET_VARIABLE", env={})
    assert "MCPQA_UNSET_VARIABLE" in str(caught.value)


ECHO = """
import json, sys
print("hello from the server", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == "die":
        sys.exit(3)
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": {}}), flush=True)
"""


def echo_server(tmp_path: Path) -> list[str]:
    script = tmp_path / "echo.py"
    script.write_text(ECHO, encoding="utf-8")
    return [sys.executable, str(script)]


def test_stdio_wire_exchanges_and_records_noise(tmp_path: Path) -> None:
    with StdioWire(echo_server(tmp_path), timeout=5) as wire:
        reply = wire.exchange({"jsonrpc": "2.0", "id": 7, "method": "x"})
    assert (reply.status, reply.message) == (None, {"jsonrpc": "2.0", "id": 7, "result": {}})
    assert wire.noise == ["hello from the server"]


def test_wire_closes_its_process_on_error(tmp_path: Path) -> None:
    wire = StdioWire(echo_server(tmp_path), timeout=5)
    started = time.monotonic()
    with pytest.raises(ConnectionError):
        wire.exchange({"jsonrpc": "2.0", "id": "die", "method": "x"})
    assert time.monotonic() - started < 5  # a closed stdout is noticed, not waited out
    wire.close()
    assert wire.process.poll() is not None


def test_silence_is_a_timeout(tmp_path: Path) -> None:
    script = tmp_path / "silent.py"
    script.write_text("import sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
    with StdioWire([sys.executable, str(script)], timeout=0.5) as wire, pytest.raises(TimeoutError):
        wire.exchange({"jsonrpc": "2.0", "id": 1, "method": "x"})


def sse(*events: dict) -> bytes:
    return b"".join(b"event: message\ndata: " + json.dumps(e).encode() + b"\n\n" for e in events)


def test_sse_reply_is_parsed() -> None:
    one = {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}
    note = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
    assert parse_sse(sse(one).decode(), 1) == one
    assert parse_sse(sse(note, one).decode(), 1) == one
    assert parse_sse("data: {\"jsonrpc\":\"2.0\",\ndata: \"id\":1,\"result\":{}}\n\n", 1) == {
        "jsonrpc": "2.0", "id": 1, "result": {}}  # several data lines are one event

    chunks = [sse(note, one)[:17], sse(note, one)[17:]]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if json.loads(request.content).get("method") == "notifications/initialized":
            return httpx2.Response(202)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=iter(chunks))

    wire = HttpWire("http://127.0.0.1:8000/mcp", transport=httpx2.MockTransport(handler))
    reply = wire.exchange({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    accepted = wire.exchange({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert (reply.status, reply.message) == (200, one)
    assert (accepted.status, accepted.message) == (202, None)


def test_session_opens_both_revisions_over_stdio() -> None:
    for revision in (LEGACY, MODERN):
        with StdioWire(ENERGY, timeout=10) as wire:
            session = Session(wire, revision)
            opened = session.open()
            tools = session.request("tools/list")
        assert set(session.capabilities) >= {"tools", "resources", "prompts"}, revision
        assert (opened.get("protocolVersion") == LEGACY) if revision == LEGACY else (MODERN in opened["supportedVersions"])
        assert len(tools.message["result"]["tools"]) == 4, revision


def test_session_opens_both_revisions_over_http(tmp_path: Path) -> None:
    with authenticated_http_server(tmp_path) as (url, token):
        for revision in (LEGACY, MODERN):
            wire = HttpWire(url, token=token, timeout=10)
            try:
                session = Session(wire, revision)
                session.open()
                tools = session.request("tools/list")
            finally:
                wire.close()
            assert tools.status == 200 and len(tools.message["result"]["tools"]) == 4, revision


def test_plugin_reports_not_applicable(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_one="""
        from mcpqa.plugin import not_applicable

        def test_needs_prompts():
            not_applicable("no prompts")
    """)
    result = pytester.runpytest_subprocess("-rs", "-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*not applicable: no prompts*"])


def test_plugin_requires_a_target(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_one="""
        def test_uses_target(mcpqa_target):
            pass
    """)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*mcpqa needs --target*"])
