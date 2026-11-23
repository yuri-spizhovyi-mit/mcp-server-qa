"""PROTO-02..08: mcpqa's conformance checks pass on energy_mcp, and each broken copy fails exactly its checks.

Every check in `mcpqa/conformance/` has at least one broken copy of `energy_mcp`
(`energy_mcp/variants.py`, `--variant NAME`) that it must catch: a check that can't fail
proves nothing (CLAUDE.md). For each copy the test compares, per check and revision:
- the failures: its checks, on every revision where the clean run passes them, and nothing else;
- the reason: each failure's message must contain the copy's `expected_message`;
- the skips: the clean run's, plus the ones the copy names, and no others.
So a check that fails for the wrong reason, or a neighbour that quietly turns "not
applicable", shows up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from energy_mcp.variants import VARIANTS
from servers import McpqaReport, authenticated_http_server, run_mcpqa

ENERGY = [sys.executable, "-m", "energy_mcp"]
CLEAN_SKIPS = {
    "stdio": {"test_header_mismatch_is_32020": "not applicable: headers exist only over HTTP"},
    "http": {"test_stdout_carries_only_messages": "not applicable: stdout exists only over stdio"},
}
# Every check some broken copy must fail. The clean runs must see exactly these checks, so a
# new check without a broken copy (one that can't be shown to fail) fails this test.
CATCHABLE = frozenset().union(*(v.expected_failures for v in VARIANTS.values()))
# mcpqa sends far more than the default 60 requests a minute; the limit itself is AUTH-06's to test.
HTTP_SERVER = ("--rate-limit", "10000")


def stdio_target(*extra: str) -> str:
    return "stdio:" + json.dumps([*ENERGY, *extra])


def run_over_http(workdir: Path, *server_args: str) -> McpqaReport:
    with authenticated_http_server(workdir, *HTTP_SERVER, *server_args) as (url, token):
        return run_mcpqa(workdir / "run", url, "--token-env", "MCPQA_TOKEN", env={"MCPQA_TOKEN": token})


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> dict[str, McpqaReport]:
    """The clean server's run on each transport, shared by the clean tests and every broken copy."""
    return {"stdio": run_mcpqa(tmp_path_factory.mktemp("clean-stdio"), stdio_target()),
            "http": run_over_http(tmp_path_factory.mktemp("clean-http"))}


def variants_for(requirement: str, transport: str = "stdio") -> list[str]:
    return [name for name, v in VARIANTS.items() if v.requirement == requirement and v.transport == transport]


def check_broken_copy(clean: dict[str, McpqaReport], workdir: Path, name: str) -> None:
    variant = VARIANTS[name]
    report = (run_mcpqa(workdir, stdio_target("--variant", name)) if variant.transport == "stdio"
              else run_over_http(workdir, "--variant", name))
    baseline = clean[variant.transport]
    expected = {key for key in baseline.runs("passed") if key[0] in variant.expected_failures}
    assert report.runs("failed") == expected, report.output[-3000:]
    for key in expected:
        assert variant.expected_message in report.messages[key], (key, report.messages[key][-1500:])
    expected_skips = baseline.runs("skipped") | {key for key in baseline.runs("passed") if key[0] in variant.expected_skips}
    assert report.runs("skipped") == expected_skips


@pytest.mark.req("PROTO-08")
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_clean_server_passes(clean: dict[str, McpqaReport], transport: str) -> None:
    report = clean[transport]
    assert report.failed == set(), report.output[-3000:]
    assert report.skipped == CLEAN_SKIPS[transport]
    assert report.passed | set(report.skipped) == CATCHABLE, "a check without a broken copy, or one that didn't run"


@pytest.mark.req("PROTO-02")
@pytest.mark.parametrize("name", variants_for("PROTO-02"))
def test_handshake_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-03")
@pytest.mark.parametrize("name", variants_for("PROTO-03") + variants_for("PROTO-03", "http"))
def test_envelope_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-04")
@pytest.mark.parametrize("name", variants_for("PROTO-04"))
def test_capability_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-05")
@pytest.mark.parametrize("name", variants_for("PROTO-05"))
def test_error_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-06")
@pytest.mark.parametrize("name", variants_for("PROTO-06"))
def test_pagination_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-07")
@pytest.mark.parametrize("name", variants_for("PROTO-07"))
def test_payload_checks_catch_their_broken_copies(clean: dict, tmp_path: Path, name: str) -> None:
    check_broken_copy(clean, tmp_path, name)


@pytest.mark.req("PROTO-08")
def test_every_broken_copy_is_tested() -> None:
    """A variant whose (transport, requirement) has no test function here would never run."""
    tested = {("stdio", r) for r in ("PROTO-02", "PROTO-03", "PROTO-04", "PROTO-05", "PROTO-06", "PROTO-07")}
    tested |= {("http", "PROTO-03")}
    assert {n for n, v in VARIANTS.items() if (v.transport, v.requirement) in tested} == set(VARIANTS)
    assert [n for n, v in VARIANTS.items() if not v.expected_message] == []  # every copy names its reason


@pytest.mark.req("PROTO-08")
@pytest.mark.parametrize(("name", "argv"), [
    ("accepts-any-cursor", ["--transport", "http", "--public-key", "k.pem", "--issuer", "https://issuer.test"]),
    ("ignores-header-mismatch", []),
])
def test_a_broken_copy_refuses_the_other_transport(name: str, argv: list[str],
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """I10: on the other transport a copy's bug isn't planted; running it would be a clean server that says otherwise."""
    from energy_mcp.__main__ import parse_args

    with pytest.raises(SystemExit) as caught:
        parse_args([*argv, "--variant", name])
    assert caught.value.code == 2
    assert f"--variant {name} is a {VARIANTS[name].transport} broken copy" in capsys.readouterr().err
