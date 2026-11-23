"""Raw transports: JSON lines over a subprocess's stdio, and plain POSTs over Streamable HTTP.

They carry exactly the bytes a check gives them, malformed ones included, and never interpret
the protocol beyond matching a reply to its request id. Every wait is bounded by `timeout`:
silence is a `TimeoutError`, a closed connection a `ConnectionError`.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx2

from mcpqa.target import Target

_NO_ID = object()


@dataclass
class Reply:
    status: int | None  # HTTP status; None over stdio
    message: dict[str, Any] | None
    headers: Mapping[str, str] = field(default_factory=dict)


def is_jsonrpc(message: Any) -> bool:
    """A JSON-RPC 2.0 request, notification or response, by shape."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return False
    return isinstance(message.get("method"), str) or ("id" in message and ("result" in message or "error" in message))


def _expected_id(message: dict[str, Any] | bytes) -> Any:
    return message.get("id", _NO_ID) if isinstance(message, dict) else None


def _matches(message: Any, expected: Any, accept_null_id: bool) -> bool:
    if not isinstance(message, dict) or "method" in message or "id" not in message:
        return False  # a request or notification from the server, not our reply
    return message["id"] == expected or (accept_null_id and message["id"] is None)


class StdioWire:
    """A server subprocess. Its first message may take `startup_timeout` (the process is still
    starting); every later wait is bounded by `timeout`, so silence is never confused with startup."""

    def __init__(self, argv: tuple[str, ...] | list[str], env: Mapping[str, str] | None = None,
                 timeout: float = 10.0, startup_timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._startup_left = max(startup_timeout, timeout)
        self.noise: list[str] = []
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self.process = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=dict(env) if env is not None else None)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)  # stdout closed

    def send(self, message: dict[str, Any] | bytes) -> None:
        data = message if isinstance(message, bytes) else json.dumps(message).encode()
        try:
            self.process.stdin.write(data + b"\n")
            self.process.stdin.flush()
        except OSError as error:
            raise ConnectionError(f"the server closed its stdin: {error}") from None

    def read(self, deadline: float | None = None) -> dict[str, Any] | None:
        """The next JSON-RPC message on stdout. Anything else (text, or JSON that isn't a JSON-RPC 2.0
        object, such as a structured log line) goes to `noise`."""
        deadline = time.monotonic() + self._bound() if deadline is None else deadline
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                break
            if line is None:
                self._lines.put(None)
                raise ConnectionError("the server closed its stdout")
            self._startup_left = 0.0  # the process has started: it wrote something
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if is_jsonrpc(message):
                return message
            self.noise.append(line.decode("utf-8", "replace").rstrip("\r\n"))
        raise TimeoutError(f"no message from the server within {self._bound()} s")

    def _bound(self) -> float:
        return self._startup_left or self.timeout

    def exchange(self, message: dict[str, Any] | bytes, headers: Mapping[str, str] | None = None,
                 accept_null_id: bool = False) -> Reply:
        self.send(message)
        expected, deadline = _expected_id(message), time.monotonic() + self._bound()
        while True:
            answer = self.read(deadline)
            if _matches(answer, expected, accept_null_id or not isinstance(message, dict)):
                return Reply(None, answer)

    def notify(self, message: dict[str, Any], headers: Mapping[str, str] | None = None) -> Reply:
        self.send(message)
        return Reply(None, None)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()

    def __enter__(self) -> StdioWire:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class MismatchedReply(Exception):
    """The server's reply does not answer the request that was sent (another id, or no reply at all)."""


def _sse_events(lines: Iterator[str]) -> Iterator[Any]:
    """Each SSE event's JSON as soon as the event is complete (data lines joined by newlines)."""
    data: list[str] = []
    for line in lines:
        line = line.rstrip("\r")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line == "" and data:
            try:
                yield json.loads("\n".join(data))
            except ValueError:
                pass
            data = []
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


def parse_sse(text: str, expected: Any) -> dict[str, Any] | None:
    """The event that answers request `expected`, or None: never some other event in its place."""
    for event in _sse_events(iter(text.replace("\r\n", "\n").split("\n"))):
        if _matches(event, expected, False):
            return event
    return None


class HttpWire:
    def __init__(self, url: str, token: str | None = None, timeout: float = 10.0,
                 transport: httpx2.BaseTransport | None = None) -> None:
        self.url, self.timeout = url, timeout
        self.session_headers: dict[str, str] = {}
        self._auth = {"authorization": f"Bearer {token}"} if token else {}
        self._client = httpx2.Client(transport=transport, timeout=timeout)

    def exchange(self, message: dict[str, Any] | bytes, headers: Mapping[str, str] | None = None,
                 accept_null_id: bool = False) -> Reply:
        """POST one message; read its reply as it streams in, within one total deadline (I3).

        A JSON-RPC reply must answer this request's id (`null` is accepted for malformed sends or
        when `accept_null_id`); anything else is `MismatchedReply`. A body that isn't JSON-RPC
        (an OAuth error body, a 202 with nothing) comes back as it is, for the check to judge.
        """
        body = message if isinstance(message, bytes) else json.dumps(message).encode()
        sent = {"content-type": "application/json", "accept": "application/json, text/event-stream",
                **self._auth, **self.session_headers, **(headers or {})}
        expected = _expected_id(message)
        allow_null = accept_null_id or not isinstance(message, dict)
        deadline = time.monotonic() + self.timeout
        try:
            with self._client.stream("POST", self.url, content=body, headers=sent) as response:
                status, received = response.status_code, dict(response.headers)
                if "text/event-stream" in response.headers.get("content-type", ""):
                    for event in _sse_events(response.iter_lines()):
                        if expected is not _NO_ID and _matches(event, expected, allow_null):
                            return Reply(status, event, received)
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"no reply from the server within {self.timeout} s")
                    if expected is _NO_ID:
                        return Reply(status, None, received)
                    raise MismatchedReply(f"the event stream ended without a reply to request {expected!r}")
                chunks = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"no complete reply from the server within {self.timeout} s")
        except httpx2.TimeoutException:
            raise TimeoutError(f"no answer from the server within {self.timeout} s") from None
        except httpx2.TransportError as error:
            raise ConnectionError(f"could not reach the server: {type(error).__name__}") from None
        try:
            parsed = json.loads(b"".join(chunks)) if chunks else None
        except ValueError:
            parsed = None
        if (expected is not _NO_ID and is_jsonrpc(parsed) and "method" not in parsed
                and not _matches(parsed, expected, allow_null)):
            raise MismatchedReply(f"the server answered id {parsed.get('id')!r} to request {expected!r}")
        return Reply(status, parsed if isinstance(parsed, dict) else None, received)

    notify = exchange

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpWire:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_wire(target: Target, timeout: float) -> StdioWire | HttpWire:
    if target.kind == "stdio":
        return StdioWire(target.argv, env=os.environ, timeout=timeout)
    return HttpWire(target.url, token=target.token, timeout=timeout)
