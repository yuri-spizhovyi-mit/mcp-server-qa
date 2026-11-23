"""SRV-08: the manual Claude run over stdio is evidenced by a checked-in screenshot."""

from __future__ import annotations

from pathlib import Path

import pytest

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

pytestmark = pytest.mark.req("SRV-08")


def test_manual_claude_screenshot_is_checked_in() -> None:
    shot = EVIDENCE / "m1-claude-stdio.png"
    assert shot.is_file(), f"missing {shot.name}: connect Claude as the README says and save the screenshot"
    assert shot.read_bytes()[:8] == PNG_SIGNATURE, f"{shot.name} is not a PNG"
    listed = (EVIDENCE / "README.md").read_text(encoding="utf-8")
    assert shot.name in listed and "(pending)" not in listed
