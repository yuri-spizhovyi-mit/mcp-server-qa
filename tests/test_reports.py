"""SRV-05: the reports://{name} resource over the synthetic report files."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS

from energy_mcp.server import REPORTS_DIR, build_server
from servers import MODES, connect

pytestmark = pytest.mark.req("SRV-05")

NAMES = ("maintenance-log", "tariff-notes", "weekly-summary")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_reports_are_listed(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        templates = (await client.list_resource_templates()).resource_templates
        resources = (await client.list_resources()).resources
    assert "reports://{name}" in {t.uri_template for t in templates}
    assert {str(r.uri) for r in resources} == {f"reports://{n}" for n in NAMES}
    assert {p.stem for p in REPORTS_DIR.glob("*.md")} == set(NAMES)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_report_reads_back_its_file(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        for name in NAMES:
            contents = (await client.read_resource(f"reports://{name}")).contents
            assert len(contents) == 1
            assert contents[0].text == (REPORTS_DIR / f"{name}.md").read_text(encoding="utf-8"), name
            assert contents[0].mime_type == "text/markdown", name


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("uri", ["reports://nosuch", "reports://Weekly-Summary", "reports://a_b",
                                 "reports://weekly-summary.md"])
async def test_unknown_or_malformed_report_is_invalid_params(mode: str, uri: str) -> None:
    async with connect(build_server(), mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(uri)
    assert caught.value.error.code == INVALID_PARAMS, caught.value.error


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_report_errors_never_echo_the_name(mode: str) -> None:
    async with connect(build_server(), mode) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource("reports://SENTINEL-7Q")
    assert caught.value.error.message == "Unknown report; available: maintenance-log, tariff-notes, weekly-summary"


def test_reports_ship_inside_the_package() -> None:
    """The files sit next to the code, so an installed wheel carries them (hatchling includes them)."""
    assert REPORTS_DIR == Path(__import__("energy_mcp").__file__).with_name("reports")
