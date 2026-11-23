# Evidence

Manual runs that a test can't do on its own. Each file is named here with what it shows and
when it was made; `tests/test_evidence.py` checks the files and that they hold nothing private.

| File | Shows | Date |
|---|---|---|
| `m1-claude-stdio.jsonl` | A recorded Claude Code run (stream-json, trimmed) that calls `energy_mcp`'s `list_meters` over stdio as an MCP tool | 2026-11-22 |
| `m1-claude-stdio.md` | The same run, readable, with the commands that produced it | 2026-11-22 |

A transcript, not a screenshot: it records which tool Claude actually called, which a picture
of the answer can't prove (F-08).
