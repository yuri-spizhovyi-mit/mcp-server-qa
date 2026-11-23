"""ENV-01..ENV-03: the two packages install from this repo, stay separate, and match the pins."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.req("ENV-01")
@pytest.mark.parametrize(("dist", "module_name"), [("energy-mcp", "energy_mcp"), ("mcpqa", "mcpqa")])
def test_both_packages_install_editable_from_this_repo(dist: str, module_name: str) -> None:
    """ENV-01: each package is its own distribution, installed editable from its src folder."""
    module = importlib.import_module(module_name)
    assert importlib.metadata.version(dist) == module.__version__ == "0.0.0"
    assert Path(module.__file__).resolve().is_relative_to(REPO / module_name / "src")


@pytest.mark.req("ENV-02")
def test_mcpqa_never_imports_or_depends_on_energy_mcp() -> None:
    """ENV-02: the reusable suite sees only what any MCP server exposes, never our server's code."""
    sources = sorted((REPO / "mcpqa" / "src").rglob("*.py"))
    assert sources, "mcpqa/src has no Python files"
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] != "energy_mcp", f"{path.relative_to(REPO)} imports {name}"
    project = tomllib.loads((REPO / "mcpqa" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for dep in project.get("dependencies", []):
        assert not re.match(r"energy[-_]mcp\b", dep), f"mcpqa depends on {dep}"


@pytest.mark.req("ENV-03")
def test_installed_versions_match_pins() -> None:
    """ENV-03: what's installed is what requirements.txt pins; a stale venv fails here, not later."""
    pins = {}
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-e")):
            continue
        name, sep, version = line.partition("==")
        assert sep, f"not an exact pin: {line!r}"
        pins[name] = version
    for name, version in pins.items():
        assert importlib.metadata.version(name) == version, f"{name}: installed != pinned {version}"
    assert pins["mcp"] == "2.3.0"
    from mcp import Client  # noqa: F401  (the v2 API the plan is written against)
    from mcp.server import MCPServer  # noqa: F401


@pytest.mark.req("PROTO-01")
def test_mcpqa_imports_no_mcp_sdk() -> None:
    """Approach A (M3): the checker speaks the protocol itself, so an SDK bug can't hide on both sides."""
    for path in sorted((REPO / "mcpqa" / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert name.split(".")[0] not in {"mcp", "mcp_types"}, f"{path.relative_to(REPO)} imports {name}"
