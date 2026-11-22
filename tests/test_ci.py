"""ENV-05: CI runs the full suite on Ubuntu and Windows with Python 3.12, and never sees a secret."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.req("ENV-05")


def _job() -> dict:
    # PyYAML reads the `on:` key as True; these tests only look at jobs and permissions.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["tests"]


def test_ci_runs_on_ubuntu_and_windows_with_python_312() -> None:
    job = _job()
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    assert job["strategy"]["fail-fast"] is False
    assert job["runs-on"] == "${{ matrix.os }}"
    setup = [s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@")]
    assert len(setup) == 1 and setup[0]["with"]["python-version"] == "3.12"


def test_ci_installs_pins_and_runs_the_full_suite() -> None:
    runs = [s.get("run", "") for s in _job()["steps"]]
    install = next(i for i, r in enumerate(runs) if "pip install -r requirements.txt" in r)
    assert "python -m pytest" in runs[install + 1:]


def test_ci_has_no_secrets_and_read_only_permissions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert yaml.safe_load(text)["permissions"] == {"contents": "read"}


def test_repo_text_files_are_lf() -> None:
    lines = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "* text=auto eol=lf" in lines
