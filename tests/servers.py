"""Helpers for tests that talk to an MCP server. Not a test module (rtm and pytest skip it)."""

from __future__ import annotations

import json
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx2
import jsonschema
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from mcp.client.streamable_http import streamable_http_client

# Imported here, not inside a helper: pytester restores sys.modules after each test, and a
# cryptography module imported during one test and re-imported in the next no longer matches
# its own classes ("Padding must be an instance of AsymmetricPadding").
from issuer import Issuer

# Client(mode=...): "legacy" negotiates 2025-11-25 with initialize; "2026-07-28" pins the new revision.
MODES = ("legacy", "2026-07-28")


def connect(server: Any, mode: str) -> Client:
    return Client(server, mode=mode)


def validate(instance: Any, schema: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError unless instance matches schema (Draft 2020-12)."""
    jsonschema.Draft202012Validator(schema).validate(instance)


class FakeClock:
    """A clock for acknowledge_alert: 2026-01-12 09:00 UTC, one minute later on each call."""

    def __init__(self, start: datetime = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        now, self.now = self.now, self.now + timedelta(minutes=1)
        return now


def stdio_params(*extra: str, env: dict[str, str] | None = None) -> StdioServerParameters:
    """Launch `python -m energy_mcp --transport stdio` as the client's subprocess.

    The SDK starts it with only a safe default environment, so the test runner's own
    ENERGY_MCP_* variables never leak in; `env` is added on top of that default.
    """
    return StdioServerParameters(command=sys.executable, args=["-m", "energy_mcp", "--transport", "stdio", *extra],
                                 env=None if env is None else get_default_environment() | env)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def http_server(port: int, *extra: str, public_key: Path, issuer: str, timeout: float = 20.0) -> Iterator[str]:
    """Run `python -m energy_mcp --transport http` on port; yield its MCP URL; stop it on exit.

    The process runs on the wall clock, so its tokens are minted with `now=time.time()`.
    """
    proc = subprocess.Popen([sys.executable, "-m", "energy_mcp", "--transport", "http", "--port", str(port),
                             "--public-key", str(public_key), "--issuer", issuer, *extra],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + timeout
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"energy_mcp exited with {proc.returncode}: {proc.stderr.read()}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    proc.kill()
                    raise RuntimeError(f"energy_mcp did not listen on {port} in {timeout}s: {proc.stderr.read()}")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stderr.close()


class ManualClock:
    """Epoch seconds that move only when a test says so; starts 2026-01-12T09:00:00Z (auth and rate limits)."""

    def __init__(self, start: float = 1768208400.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def writer() -> Any:
    """An in-process caller that may acknowledge any alert (the default identity is read-only, AUTH-D3)."""
    from energy_mcp.access import Identity
    from energy_mcp.scopes import READ_SCOPES

    return Identity("op-1", READ_SCOPES | {"alerts:write"})


BASE = "http://127.0.0.1:8000"  # the SDK's host check refuses other hosts (spike, M2 plan)
MCP_URL = f"{BASE}/mcp"
METADATA_URL = f"{BASE}/.well-known/oauth-protected-resource/mcp"


@asynccontextmanager
async def running(server: Any) -> AsyncIterator[None]:
    """Start the SDK's Streamable HTTP session manager (ASGITransport sends no lifespan events)."""
    async with server.session_manager.run():
        yield


def http(app: Any, token: str | None = None, scheme: str = "Bearer") -> httpx2.AsyncClient:
    """An HTTP client that talks to `app` in-process, optionally with a bearer token."""
    headers = {"authorization": f"{scheme} {token}"} if token is not None else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers)


def mcp_over(client: httpx2.AsyncClient, mode: str) -> Client:
    return Client(streamable_http_client(MCP_URL, http_client=client), mode=mode)


def rpc(method: str, params: Any = None, request_id: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def tool_call(name: str, arguments: Any = None) -> dict[str, Any]:
    return rpc("tools/call", {"name": name, "arguments": {} if arguments is None else arguments})


async def post(client: httpx2.AsyncClient, body: Any, *, raw: bytes | None = None,
               content_type: str = "application/json", **headers: str) -> httpx2.Response:
    content = raw if raw is not None else json.dumps(body).encode()
    return await client.post("/mcp", content=content, headers={
        "content-type": content_type, "accept": "application/json, text/event-stream", **headers})


ENVELOPE = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}


def modern_headers(method: str, name: str | None = None) -> dict[str, str]:
    """The headers a raw 2026-07-28 request needs; the SDK checks they mirror the body (-32020)."""
    headers = {"mcp-protocol-version": "2026-07-28", "mcp-method": method}
    return headers | ({"mcp-name": name} if name else {})


async def post_call(client: httpx2.AsyncClient, name: str, arguments: Any = None) -> httpx2.Response:
    """A complete raw 2026-07-28 tools/call that the SDK would run.

    A refusal is only proof that the edge refused if the request would have worked without
    it; an incomplete request is refused by the SDK anyway, and the test could not fail.
    """
    body = rpc("tools/call", {"name": name, "arguments": {} if arguments is None else arguments, "_meta": ENVELOPE})
    return await post(client, body, **modern_headers("tools/call", name))


class RawStdio:
    """Raw JSON lines to a subprocess's stdin, answers read from its stdout (for protocol edge cases).

    `read()` returns the next JSON message, or None after `timeout`. The first read may also take
    `startup_timeout`, because the process is still starting: importing the SDK takes seconds on
    a busy Windows machine (F-16). Lines on stdout that are not JSON go to `noise`. The process
    is killed on exit, whatever state it is in.
    """

    def __init__(self, argv: list[str], env: dict[str, str] | None = None, timeout: float = 5.0,
                 startup_timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._startup_left = max(startup_timeout, timeout)
        self.noise: list[str] = []
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env or get_default_environment())
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def send(self, message: Any) -> None:
        data = message if isinstance(message, bytes) else json.dumps(message).encode()
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def read(self) -> dict | None:
        deadline = time.monotonic() + (self._startup_left or self.timeout)
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                return None
            self._startup_left = 0.0  # the process has started: it wrote something
            try:
                return json.loads(line)
            except ValueError:
                self.noise.append(line.decode("utf-8", "replace").rstrip())
        return None

    def __enter__(self) -> "RawStdio":
        return self

    def __exit__(self, *exc: object) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout):
            stream.close()


@contextmanager
def authenticated_http_server(tmp_path: Path, *extra: str) -> Iterator[tuple[str, str]]:
    """A real HTTP server process with its own issuer key; yields its URL and a wall-clock read token."""
    port = free_port()
    issuer = Issuer(audience=f"http://127.0.0.1:{port}/mcp")
    key = tmp_path / "issuer.pem"
    key.write_bytes(issuer.public_pem)
    with http_server(port, *extra, public_key=key, issuer=issuer.issuer) as url:
        yield url, issuer.mint(scope="meters:read alerts:read reports:read", now=time.time())


@dataclass
class McpqaReport:
    passed: set[str]
    failed: set[str]
    skipped: dict[str, str]
    output: str


def run_mcpqa(pytester: Any, target: str, *extra: str, env: dict[str, str] | None = None) -> McpqaReport:
    """Run mcpqa's conformance checks against `target` as an outsider would, in a clean pytest process.

    Results are read from a JUnit XML report and grouped by check name (a check that fails on
    one revision counts as failed). `-c` points at an empty ini, so the repo's own pytest
    settings play no part.
    """
    ini = pytester.makeini("[pytest]\n")
    report = pytester.path / "mcpqa-report.xml"
    for name, value in (env or {}).items():
        pytester._monkeypatch.setenv(name, value)
    result = pytester.runpytest_subprocess("-c", str(ini), "--pyargs", "mcpqa.conformance", "--target", target,
                                           "--mcpqa-timeout", "5", "-p", "no:cacheprovider",
                                           f"--junitxml={report}", *extra)
    passed, failed, skipped = set(), set(), {}
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        name = case.get("name", "").split("[", 1)[0]
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(name)
        elif (skip := case.find("skipped")) is not None:
            skipped[name] = skip.get("message", "")
        else:
            passed.add(name)
    return McpqaReport(passed - failed, failed, {k: v for k, v in skipped.items() if k not in failed},
                       "\n".join(result.outlines))
