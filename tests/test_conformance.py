"""PROTO-02..08: mcpqa's conformance checks pass on energy_mcp, and each broken copy fails exactly its checks.

Every check in `mcpqa/conformance/` has at least one broken copy of `energy_mcp`
(`energy_mcp/variants.py`, `--variant NAME`) that it must catch: a check that can't fail
proves nothing (CLAUDE.md). Comparing the *set* of failed checks also catches a check that
fails for the wrong reason, or a broken copy that breaks more than it should.
"""

from __future__ import annotations

import json
import sys

import pytest

from energy_mcp.variants import VARIANTS
from servers import run_mcpqa

ENERGY = [sys.executable, "-m", "energy_mcp"]
CLEAN_STDIO_SKIPS = {"test_header_mismatch_is_32020": "not applicable: headers exist only over HTTP"}
# Every check some broken copy must fail. The clean run must see exactly these checks, so a
# new check without a broken copy (one that can't be shown to fail) fails this test.
CATCHABLE = frozenset().union(*(v.expected_failures for v in VARIANTS.values()))


def stdio_target(*extra: str) -> str:
    return "stdio:" + json.dumps([*ENERGY, *extra])


def variants_for(requirement: str) -> list[str]:
    return [name for name, v in VARIANTS.items() if v.requirement == requirement and v.transport == "stdio"]


def check_broken_copy(pytester: pytest.Pytester, name: str) -> None:
    report = run_mcpqa(pytester, stdio_target("--variant", name))
    assert report.failed == VARIANTS[name].expected_failures, report.output[-3000:]


@pytest.mark.req("PROTO-08")
def test_clean_server_passes_over_stdio(pytester: pytest.Pytester) -> None:
    report = run_mcpqa(pytester, stdio_target())
    assert report.failed == set(), report.output[-3000:]
    assert report.skipped == CLEAN_STDIO_SKIPS
    assert report.passed | set(report.skipped) == CATCHABLE, "a check without a broken copy, or one that didn't run"


@pytest.mark.req("PROTO-02")
@pytest.mark.parametrize("name", variants_for("PROTO-02"))
def test_handshake_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-03")
@pytest.mark.parametrize("name", variants_for("PROTO-03"))
def test_envelope_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-04")
@pytest.mark.parametrize("name", variants_for("PROTO-04"))
def test_capability_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-05")
@pytest.mark.parametrize("name", variants_for("PROTO-05"))
def test_error_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-06")
@pytest.mark.parametrize("name", variants_for("PROTO-06"))
def test_pagination_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-07")
@pytest.mark.parametrize("name", variants_for("PROTO-07"))
def test_payload_checks_catch_their_broken_copies(pytester: pytest.Pytester, name: str) -> None:
    check_broken_copy(pytester, name)


@pytest.mark.req("PROTO-08")
def test_every_broken_copy_is_tested() -> None:
    """A variant whose requirement has no test function here would never run."""
    tested = {"PROTO-02", "PROTO-03", "PROTO-04", "PROTO-05", "PROTO-06", "PROTO-07"}
    stdio = {n for n, v in VARIANTS.items() if v.transport == "stdio"}
    assert {n for n in stdio if VARIANTS[n].requirement in tested} == stdio
