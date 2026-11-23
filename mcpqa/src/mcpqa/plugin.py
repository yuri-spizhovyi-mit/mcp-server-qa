"""The mcpqa pytest plugin: run `pytest --pyargs mcpqa.conformance --target <server>`.

Options: `--target` (PROTO-D3), `--token-env`, `--protocol-version` (repeatable; default
both), `--allow-writes`, `--mcpqa-timeout`. A check whose precondition the target lacks
calls `not_applicable(reason)`: it is reported as skipped with "not applicable: <reason>",
never as a silent pass.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import NoReturn

import pytest

from mcpqa.protocol import REVISIONS, AuthRefused, NotServed, Session
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


USAGE_EXIT = pytest.ExitCode.USAGE_ERROR
_TRIED = pytest.StashKey[set[str]]()
_SERVED = pytest.StashKey[set[str]]()


def _open_wire(target: Target, timeout: float) -> StdioWire | HttpWire:
    try:
        return open_wire(target, timeout)
    except OSError as error:  # a stdio command that can't be started is one usage error, not one per check
        pytest.exit(f"mcpqa: could not start the stdio target {target.argv[0]!r}: {error.strerror or error}",
                    returncode=USAGE_EXIT)


@pytest.fixture(scope="session")
def session(revision: str, mcpqa_target: Target, mcpqa_timeout: float, pytestconfig: pytest.Config) -> Iterator[Session]:
    """One open session per revision for the whole run (a stdio process serves one revision).

    Only a genuine "not served" answer makes a revision's checks not applicable (C1). Any other
    failure to open (an internal error, a malformed or missing result, a wrong URL, silence)
    errors every check of that revision, so a broken or unreachable server never looks green.
    """
    pytestconfig.stash.setdefault(_TRIED, set()).add(revision)
    wire = _open_wire(mcpqa_target, mcpqa_timeout)
    try:
        opened = Session(wire, revision)
        try:
            opened.open()
        except AuthRefused as error:
            pytest.exit(f"mcpqa: the target answered {error}: the bearer token is missing or refused; "
                        "name the variable that holds it with --token-env", returncode=USAGE_EXIT)
        except NotServed as error:
            if revision in (pytestconfig.getoption("protocol_version") or []):
                pytest.exit(f"mcpqa: the target does not serve {revision}, which --protocol-version asks for "
                            f"({error})", returncode=USAGE_EXIT)
            not_applicable(f"the target does not serve {revision} ({error})")
        pytestconfig.stash.setdefault(_SERVED, set()).add(revision)
        yield opened
    finally:
        wire.close()


@pytest.fixture
def fresh_wire(mcpqa_target: Target, mcpqa_timeout: float) -> Iterator[StdioWire | HttpWire]:
    """A new connection (a new process over stdio), for checks that must open their own session."""
    wire = _open_wire(mcpqa_target, mcpqa_timeout)
    try:
        yield wire
    finally:
        wire.close()


@pytest.fixture
def new_wire(mcpqa_target: Target, mcpqa_timeout: float) -> Iterator[Callable[[], StdioWire | HttpWire]]:
    """Open as many fresh connections as a check needs; all are closed after it."""
    opened: list[StdioWire | HttpWire] = []

    def make() -> StdioWire | HttpWire:
        opened.append(_open_wire(mcpqa_target, mcpqa_timeout))
        return opened[-1]

    try:
        yield make
    finally:
        for wire in opened:
            wire.close()


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """A target that serves none of the revisions it was checked on fails the run: nothing was checked."""
    tried = session.config.stash.get(_TRIED, set())
    if tried and not session.config.stash.get(_SERVED, set()):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"mcpqa: the target serves none of the revisions checked ({', '.join(sorted(tried))})",
                                red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
