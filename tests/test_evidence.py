"""SRV-08: a real Claude client connected to the server over stdio and called a tool.

The evidence is a recorded Claude Code run (`docs/evidence/m1-claude-stdio.jsonl`, trimmed
from `claude -p --output-format stream-json`). It is scored on the tool calls, not on the
answer's words: the first attempt at this evidence was a screenshot of a session where the
server was not connected, and Claude produced the right table anyway by running the server
through a shell command (F-08). `test_a_run_that_bypassed_mcp_is_rejected` replays that case.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from energy_mcp.world import build_world

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence"
TRANSCRIPT = EVIDENCE / "m1-claude-stdio.jsonl"
TOOL = "mcp__energy-mcp__list_meters"

pytestmark = pytest.mark.req("SRV-08")


def mcp_call_problems(events: list[dict]) -> list[str]:
    """Why a transcript does not show Claude calling energy-mcp's list_meters for SITE-02; empty if it does."""
    problems = []
    calls = {e["id"]: e for e in events if e["kind"] == "tool_use" and e["name"] == TOOL}
    if not any(c["input"] == {"site_id": "SITE-02"} for c in calls.values()):
        problems.append(f"no {TOOL} call with site_id SITE-02")
    results = [e for e in events if e["kind"] == "tool_result" and e["tool_use_id"] in calls]
    if not results or any(r["is_error"] for r in results):
        problems.append(f"no successful result from {TOOL}")
    else:
        served = {m["meter_id"] for r in results for m in json.loads(r["content"])["meters"]}
        expected = {m.meter_id for m in build_world().meters if m.site_id == "SITE-02"}
        if served != expected:
            problems.append(f"the server returned {sorted(served)}, the seeded world has {sorted(expected)}")
    if not any(e["kind"] == "result" and e["is_error"] is False for e in events):
        problems.append("the run did not finish successfully")
    return problems


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_recorded_claude_run_called_the_server_over_mcp() -> None:
    events = load(TRANSCRIPT)
    session = next(e for e in events if e["kind"] == "session")
    assert session["client"] == "Claude Code" and session["mcp_server"]["name"] == "energy-mcp"
    assert mcp_call_problems(events) == []


def test_a_run_that_bypassed_mcp_is_rejected() -> None:
    """The 18:20 screenshot case: right answer, no MCP tool call."""
    bypass = [
        {"kind": "session", "client": "Claude Code", "mcp_server": {"name": "energy-mcp", "status": "failed"}},
        {"kind": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "python -m energy_mcp ..."}},
        {"kind": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "MTR-0002 MTR-0008 MTR-0013"},
        {"kind": "answer", "text": "SITE-02 has MTR-0002, MTR-0008 and MTR-0013."},
        {"kind": "result", "is_error": False, "num_turns": 3},
    ]
    assert mcp_call_problems(bypass) == [f"no {TOOL} call with site_id SITE-02", f"no successful result from {TOOL}"]


def test_evidence_is_listed_and_holds_nothing_private() -> None:
    """The repo is public: no local paths, account names or other connectors in the evidence."""
    listed = (EVIDENCE / "README.md").read_text(encoding="utf-8")
    for name in ("m1-claude-stdio.jsonl", "m1-claude-stdio.md"):
        assert name in listed, name
        text = (EVIDENCE / name).read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\\\?Users|/home/|@[\w-]+\.\w", text), name
    assert "(pending)" not in listed


def test_readme_desktop_config_is_valid_json() -> None:
    """The owner pastes this block into claude_desktop_config.json; it must parse."""
    blocks = re.findall(r"```json\n(.*?)```", (REPO / "README.md").read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1
    server = json.loads(blocks[0])["mcpServers"]["energy-mcp"]
    assert server["command"].endswith("python.exe") and server["args"] == ["-m", "energy_mcp"]
