"""The mcpqa pytest plugin: run `pytest --pyargs mcpqa.conformance --target <server>`.

Options: `--target` (PROTO-D3), `--token-env`, `--protocol-version` (repeatable; default
both), `--allow-writes`, `--mcpqa-timeout`. A check whose precondition the target lacks
calls `not_applicable(reason)`: it is reported as skipped with "not applicable: <reason>",
never as a silent pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NoReturn

import pytest

from mcpqa.protocol import REVISIONS, ProtocolError, Session
from mcpqa.target import Target, parse_target
from mcpqa.wire import HttpWire, StdioWire, open_wire


def not_applicable(reason: str) -> NoReturn:
    pytest.skip(f"not applicable: {reason}")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mcpqa", "mcpqa: MCP server conformance")
    group.addoption("--target", help='the server: stdio:["cmd", "arg"], stdio:cmd arg, or http://host:port/mcp')
    group.addoption("--token-env", help="environment variable holding a bearer token for an HTTP target")
    group.addoption("--protocol-version", action="append", choices=REVISIONS,
                    help="revision to check (repeatable; default: both)")
    group.addoption("--allow-writes", action="store_true", help="also call tools not annotated readOnlyHint")
    group.addoption("--mcpqa-timeout", type=float, default=10.0, help="upper bound for any wait, seconds")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "mcpqa_revisions(*versions): run a check only on these revisions")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "revision" not in metafunc.fixturenames:
        return
    versions = metafunc.config.getoption("protocol_version") or list(REVISIONS)
    marker = metafunc.definition.get_closest_marker("mcpqa_revisions")
    if marker is not None:
        versions = [v for v in versions if v in marker.args]
    metafunc.parametrize("revision", versions, scope="session")


@pytest.fixture(scope="session")
def mcpqa_timeout(pytestconfig: pytest.Config) -> float:
    return pytestconfig.getoption("mcpqa_timeout")


@pytest.fixture(scope="session")
def allow_writes(pytestconfig: pytest.Config) -> bool:
    return pytestconfig.getoption("allow_writes")


@pytest.fixture(scope="session")
def mcpqa_target(pytestconfig: pytest.Config) -> Target:
    text = pytestconfig.getoption("target")
    if not text:
        raise pytest.UsageError('mcpqa needs --target, e.g. --target "stdio:python -m my_server"')
    try:
        return parse_target(text, token_env=pytestconfig.getoption("token_env"))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None


@pytest.fixture(scope="session")
def session(revision: str, mcpqa_target: Target, mcpqa_timeout: float) -> Iterator[Session]:
    """One open session per revision for the whole run (a stdio process serves one revision)."""
    wire = open_wire(mcpqa_target, mcpqa_timeout)
    try:
        opened = Session(wire, revision)
        try:
            opened.open()
        except ProtocolError as error:
            not_applicable(f"the target does not serve {revision} ({error})")
        yield opened
    finally:
        wire.close()


@pytest.fixture
def fresh_wire(mcpqa_target: Target, mcpqa_timeout: float) -> Iterator[StdioWire | HttpWire]:
    """A new connection (a new process over stdio), for checks that must open their own session."""
    wire = open_wire(mcpqa_target, mcpqa_timeout)
    try:
        yield wire
    finally:
        wire.close()
