"""Helpers for tests that talk to an MCP server. Not a test module (rtm and pytest skip it)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema
from mcp import Client, StdioServerParameters

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


def stdio_params(*extra: str) -> StdioServerParameters:
    """Launch `python -m energy_mcp --transport stdio` as the client's subprocess."""
    return StdioServerParameters(command=sys.executable, args=["-m", "energy_mcp", "--transport", "stdio", *extra])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def http_server(port: int, timeout: float = 20.0) -> Iterator[str]:
    """Run `python -m energy_mcp --transport http` on port; yield its MCP URL; stop it on exit."""
    proc = subprocess.Popen([sys.executable, "-m", "energy_mcp", "--transport", "http", "--port", str(port)],
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
