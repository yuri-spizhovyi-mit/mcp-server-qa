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
from collections.abc import Mapping
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
        """The next JSON message on stdout; non-JSON lines go to `noise`."""
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
                return json.loads(line)
            except ValueError:
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


def parse_sse(text: str, expected: Any) -> dict[str, Any] | None:
    """The event whose JSON `id` is `expected`, else the first JSON event (data lines joined by newlines)."""
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].removeprefix(" ") for line in block.split("\n") if line.startswith("data:")]
        if data:
            try:
                events.append(json.loads("\n".join(data)))
            except ValueError:
                continue
    for event in events:
        if isinstance(event, dict) and event.get("id", _NO_ID) == expected and "method" not in event:
            return event
    return events[0] if events else None


class HttpWire:
    def __init__(self, url: str, token: str | None = None, timeout: float = 10.0,
                 transport: httpx2.BaseTransport | None = None) -> None:
        self.url, self.timeout = url, timeout
        self.session_headers: dict[str, str] = {}
        self._auth = {"authorization": f"Bearer {token}"} if token else {}
        self._client = httpx2.Client(transport=transport, timeout=timeout)

    def exchange(self, message: dict[str, Any] | bytes, headers: Mapping[str, str] | None = None,
                 accept_null_id: bool = False) -> Reply:
        body = message if isinstance(message, bytes) else json.dumps(message).encode()
        sent = {"content-type": "application/json", "accept": "application/json, text/event-stream",
                **self._auth, **self.session_headers, **(headers or {})}
        try:
            response = self._client.post(self.url, content=body, headers=sent)
        except httpx2.TimeoutException:
            raise TimeoutError(f"no answer from the server within {self.timeout} s") from None
        except httpx2.TransportError as error:
            raise ConnectionError(f"could not reach the server: {type(error).__name__}") from None
        content_type = response.headers.get("content-type", "")
        parsed: Any = None
        if response.content:
            if "text/event-stream" in content_type:
                parsed = parse_sse(response.text, _expected_id(message))
            else:
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = None
        return Reply(response.status_code, parsed if isinstance(parsed, dict) else None, dict(response.headers))

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
