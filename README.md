# mcp-server-qa

QA for the tool layer of AI agents: a small MCP server and the test suite that proves it follows the protocol, keeps its tool contracts, resists attacks, and works for an agent that uses it.

Status: M1 server (in progress). Plan: [docs/plan.md](docs/plan.md).

## Run the server

Windows PowerShell, in the repo folder, after `python -m venv .venv` and
`.\.venv\Scripts\python -m pip install -r requirements.txt`:

```powershell
.\.venv\Scripts\python -m energy_mcp                               # stdio (what Claude launches)
.\.venv\Scripts\python -m energy_mcp --transport http --port 8000  # Streamable HTTP at http://127.0.0.1:8000/mcp
```

All data is synthetic (seed 42): 20 meters at 6 sites, a week of hourly readings, 50 alerts.

## Try it with Claude (no API key)

Replace `C:\path\to\mcp-server-qa` with the full path of this folder.

**Claude Code** (Windows PowerShell):

```powershell
claude mcp add energy-mcp -- C:\path\to\mcp-server-qa\.venv\Scripts\python.exe -m energy_mcp
```

Then start `claude` and ask: "Using energy-mcp, list the meters at SITE-02." To remove it:
`claude mcp remove energy-mcp`.

**Claude desktop app**: Settings > Developer > Edit Config opens `claude_desktop_config.json`;
add the server and restart the app:

```json
{
  "mcpServers": {
    "energy-mcp": {
      "command": "C:\\path\\to\\mcp-server-qa\\.venv\\Scripts\\python.exe",
      "args": ["-m", "energy_mcp"]
    }
  }
}
```

A recorded run (Claude Code calling `list_meters` over stdio) is in [docs/evidence/](docs/evidence/m1-claude-stdio.md).
