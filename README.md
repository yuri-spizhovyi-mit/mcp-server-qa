# mcp-server-qa

QA for the tool layer of AI agents: a small MCP server and the test suite that proves it follows the protocol, keeps its tool contracts, resists attacks, and works for an agent that uses it.

Status: M1 server done; M2 auth in progress. Plan: [docs/plan.md](docs/plan.md).

## Run the server

Windows PowerShell, in the repo folder, after `python -m venv .venv` and
`.\.venv\Scripts\python -m pip install -r requirements.txt`:

```powershell
.\.venv\Scripts\python -m energy_mcp                               # stdio (what Claude launches), read-only
```

All data is synthetic (seed 42): 20 meters at 6 sites, a week of hourly readings, 50 alerts.

**Who the caller is on stdio.** The server reads two environment variables:

- `ENERGY_MCP_SCOPES`: space-separated. The default is `meters:read alerts:read reports:read`, which is read-only.
- `ENERGY_MCP_OPERATOR`: default `local-operator`. It is recorded as `acknowledged_by`.

Set `ENERGY_MCP_SCOPES` to `meters:read alerts:read reports:read alerts:write` to let `acknowledge_alert` write. An unknown scope stops the server at startup.

**HTTP** is an OAuth 2.1 resource server, and there is no unauthenticated mode. It needs your token issuer's public key (a PEM file) and its name:

```powershell
.\.venv\Scripts\python -m energy_mcp --transport http --port 8000 --public-key issuer.pem --issuer https://issuer.example
```

- It serves Streamable HTTP at `http://127.0.0.1:8000/mcp`. That URL is also the audience its tokens must carry; change it with `--resource`.
- It publishes RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`.
- It accepts RS256 `at+jwt` access tokens. Each tool, resource and prompt needs its own scope, and `alerts:write:ALR-0042` acknowledges only that one alert.
- The rate limit is 60 requests per minute per caller (`--rate-limit`, `--rate-window`).

The tests mint their own tokens; see `tests/issuer.py`.

## Check any MCP server with mcpqa

`mcpqa` is a pytest plugin that checks a server's protocol conformance on both revisions (2025-11-25 and 2026-07-28). It speaks the protocol with its own client, not an SDK. Run it in Windows PowerShell, in the repo folder:

```powershell
.\.venv\Scripts\python -m pytest --pyargs mcpqa.conformance -rs --target 'stdio:["C:\\path\\to\\python.exe", "-m", "energy_mcp"]'
```

- **Targets:**
  - `stdio:` followed by a JSON array, which is exact. Use this for Windows paths.
  - `stdio:` followed by a command line.
  - An `http://` URL, with `--token-env NAME` naming the environment variable that holds the bearer token. The token never goes on the command line.
- **Revisions:** `--protocol-version` picks one revision; the default is both.
- **Read-only:** it calls only tools annotated read-only.
- **Not applicable:** a check that doesn't apply to the target is reported as `not applicable: <reason>`, never as a silent pass.
- **Proof the checks can fail:** each check has a broken copy of `energy_mcp` that it must catch (`energy_mcp --variant NAME`, tests only). See `tests/test_conformance.py`.

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
