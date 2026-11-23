# M1: Claude Code calls energy_mcp over stdio

Recorded 2026-11-22 on Windows 11. Readable copy of
[`m1-claude-stdio.jsonl`](m1-claude-stdio.jsonl); `tests/test_evidence.py` checks the JSONL.

**Setup** (Windows PowerShell, in the repo folder):

```powershell
claude mcp add energy-mcp -- <repo>\.venv\Scripts\python.exe -m energy_mcp
claude -p "Using energy-mcp, list the meters at SITE-02." --output-format stream-json --verbose --allowedTools "mcp__energy-mcp__list_meters"
```

Claude Code 2.1.330, model `claude-opus-5-5`, on the owner's Claude plan (no API key).
The server was registered in local scope and started by Claude Code as a stdio subprocess.

**What happened** (3 turns, finished without error):

1. Claude searched its deferred tools for "energy-mcp meters" and found the four energy-mcp
   tools: `list_meters`, `acknowledge_alert`, `get_readings`, `list_alerts`.
2. Claude called **`mcp__energy-mcp__list_meters`** with `{"site_id": "SITE-02"}`.
3. The server answered (no error):

   ```json
   {"meters":[{"meter_id":"MTR-0002","site_id":"SITE-02","site_name":"Pump House 2","supply":"single-phase"},{"meter_id":"MTR-0008","site_id":"SITE-02","site_name":"Pump House 2","supply":"three-phase"},{"meter_id":"MTR-0013","site_id":"SITE-02","site_name":"Pump House 2","supply":"single-phase"}]}
   ```

4. Claude's answer:

   > SITE-02 (Pump House 2) has 3 meters, according to `list_meters` on energy-mcp:
   >
   > | Meter ID | Supply |
   > |---|---|
   > | MTR-0002 | single-phase |
   > | MTR-0008 | three-phase |
   > | MTR-0013 | single-phase |

**Trimmed from the raw stream:** session IDs, local paths, hook and timing events, and the
other MCP servers and tools configured on the machine. Nothing else was edited.
