"""PROTO-01: mcpqa's core: target strings, raw wires, protocol sessions and the pytest plugin."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx2
import pytest

from mcpqa.protocol import LEGACY, MODERN, Session
from mcpqa.target import Target, parse_target
from mcpqa.wire import HttpWire, MismatchedReply, StdioWire, parse_sse
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
    with StdioWire([sys.executable, str(script)], timeout=0.5, startup_timeout=0.5) as wire, pytest.raises(TimeoutError):
        wire.exchange({"jsonrpc": "2.0", "id": 1, "method": "x"})


def sse(*events: dict) -> bytes:
    return b"".join(b"event: message\ndata: " + json.dumps(e).encode() + b"\n\n" for e in events)


def test_sse_reply_is_parsed() -> None:
    one = {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}
    note = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
    assert parse_sse(sse(one).decode(), 1) == one
    assert parse_sse(sse(note, one).decode(), 1) == one
    assert parse_sse(sse(note).decode(), 1) is None  # no fallback to an event that isn't the reply
    assert parse_sse("data: {\"jsonrpc\":\"2.0\",\ndata: \"id\":1,\"result\":{}}\n\n", 1) == {
        "jsonrpc": "2.0", "id": 1, "result": {}}  # several data lines are one event

    blob = sse(note, one)
    cut = blob.index(b'"result"')
    chunks = [blob[:cut], blob[cut:]]  # split inside the reply's own data line

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


FAKE = Path(__file__).with_name("fake_stdio_server.py")


def fake_target(mode: str) -> str:
    return "stdio:" + json.dumps([sys.executable, str(FAKE), mode])


def run_raw(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    ini = pytester.makeini("[pytest]\n")
    return pytester.runpytest_subprocess("-c", str(ini), "--pyargs", "mcpqa.conformance", "--mcpqa-timeout", "5",
                                         "-p", "no:cacheprovider", "-rA", *args)


def test_a_server_that_fails_everything_fails_the_run(pytester: pytest.Pytester) -> None:
    """C1: an error to every request is a broken server, not one that 'doesn't apply'."""
    result = run_raw(pytester, "--target", fake_target("internal"))
    assert result.ret != 0
    outcomes = result.parseoutcomes()
    assert outcomes.get("failed", 0) + outcomes.get("errors", 0) > 0 and not outcomes.get("passed")


def test_a_wrong_http_path_fails_the_run(pytester: pytest.Pytester, tmp_path: Path) -> None:
    with authenticated_http_server(tmp_path, "--rate-limit", "10000") as (url, token):
        pytester._monkeypatch.setenv("MCPQA_TOKEN", token)
        result = run_raw(pytester, "--target", url.replace("/mcp", "/nope"), "--token-env", "MCPQA_TOKEN")
    assert result.ret != 0
    assert not result.parseoutcomes().get("passed")


def test_a_refused_token_is_one_clear_message(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """I5: a 401 stops the run with one message naming --token-env, not a traceback per check."""
    with authenticated_http_server(tmp_path) as (url, _):
        result = run_raw(pytester, "--target", url)
    assert result.ret != 0
    text = "\n".join(result.outlines + result.errlines)
    assert "HTTP 401" in text and "--token-env" in text
    assert "AttributeError" not in text and "Traceback" not in text


def test_an_older_negotiated_version_is_not_applicable(pytester: pytest.Pytester) -> None:
    """I1: a correct 2025-06-18 server doesn't serve 2025-11-25; it isn't broken. Serving neither revision
    mcpqa checks still fails the run, with one clear message."""
    result = run_raw(pytester, "--target", fake_target("legacy-old"))
    text = "\n".join(result.outlines)
    assert not result.parseoutcomes().get("failed")
    assert "negotiated 2025-06-18" in text
    assert result.ret != 0 and "serves none of the revisions" in "\n".join(result.outlines + result.errlines)


def test_an_explicit_revision_the_target_does_not_serve_is_an_error(pytester: pytest.Pytester) -> None:
    result = run_raw(pytester, "--target", fake_target("legacy-only"), "--protocol-version", "2026-07-28")
    assert result.ret != 0
    assert "does not serve 2026-07-28" in "\n".join(result.outlines + result.errlines)


def test_a_stdio_target_that_cannot_start_is_one_clear_message(pytester: pytest.Pytester) -> None:
    result = run_raw(pytester, "--target", 'stdio:["mcpqa-no-such-program-xyz"]')
    text = "\n".join(result.outlines + result.errlines)
    assert result.ret != 0 and "could not start" in text and "Traceback" not in text


def test_shell_form_rejects_windows_backslashes() -> None:
    """I6: shlex drops unquoted backslashes; refuse instead of running a mangled path."""
    with pytest.raises(ValueError, match="JSON array"):
        parse_target(r"stdio:C:\Users\me\python.exe -m energy_mcp")


JSON_LOGGER = """
import json, sys
print(json.dumps({"level": "info", "msg": "starting"}), flush=True)
print("42", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": {}}), flush=True)
"""


def test_json_that_is_not_a_message_is_noise(tmp_path: Path) -> None:
    """I2: a structured logger writing JSON to stdout breaks stdio as surely as plain text does."""
    script = tmp_path / "logger.py"
    script.write_text(JSON_LOGGER, encoding="utf-8")
    with StdioWire([sys.executable, str(script)], timeout=5) as wire:
        reply = wire.exchange({"jsonrpc": "2.0", "id": 3, "method": "x"})
    assert reply.message == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert wire.noise == ['{"level": "info", "msg": "starting"}', "42"]


class _LingeringSSE(BaseHTTPRequestHandler):
    """Answers at once, then keeps the SSE stream open with comment pings (legal: closing is only a SHOULD)."""

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"data: " + json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}).encode() + b"\n\n")
        self.wfile.flush()
        try:
            for _ in range(40):
                time.sleep(0.25)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass  # the client hung up, as it should once it has its reply

    def log_message(self, *args: object) -> None:
        pass


def test_http_reply_is_bounded_even_if_the_stream_stays_open() -> None:
    """I3a: the reply is taken as soon as it arrives; a total deadline bounds the wait."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LingeringSSE)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with HttpWire(f"http://127.0.0.1:{server.server_port}/mcp", timeout=2) as wire:
            started = time.monotonic()
            reply = wire.exchange({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
            elapsed = time.monotonic() - started
    finally:
        server.shutdown()
    assert reply.message == {"jsonrpc": "2.0", "id": 4, "result": {}}
    assert elapsed < 2, f"took {elapsed:.1f} s"


def test_http_reply_with_another_id_is_an_error() -> None:
    """I3b: a reply must answer the request that was sent."""
    def wrong_id(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 99, "result": {}})

    def only_a_notification(request: httpx2.Request) -> httpx2.Response:
        note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(note))

    for handler in (wrong_id, only_a_notification):
        wire = HttpWire("http://127.0.0.1:8000/mcp", transport=httpx2.MockTransport(handler))
        with pytest.raises(MismatchedReply):
            wire.exchange({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
