"""A fake MCP server over stdio for mcpqa's own edge cases (not a test module).

`python fake_stdio_server.py MODE`, where MODE is:
- internal: every request gets -32603;
- legacy-old: initialize negotiates 2025-06-18 (a correct older server); everything else -32601;
- legacy-only: serves 2025-11-25 initialize and ping, answers server/discover with -32601.
"""

import json
import sys

MODE = sys.argv[1]


def answer(message: dict) -> dict | None:
    if "id" not in message or "method" not in message:
        return None  # a notification
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    method = message["method"]
    if MODE == "internal":
        return {**reply, "error": {"code": -32603, "message": "Internal error"}}
    if method == "initialize":
        version = "2025-06-18" if MODE == "legacy-old" else "2025-11-25"
        return {**reply, "result": {"protocolVersion": version, "capabilities": {},
                                    "serverInfo": {"name": "fake", "version": "0"}}}
    if method == "ping" and MODE == "legacy-only":
        return {**reply, "result": {}}
    return {**reply, "error": {"code": -32601, "message": "Method not found"}}


for line in sys.stdin:
    try:
        reply = answer(json.loads(line))
    except ValueError:
        reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    if reply is not None:
        print(json.dumps(reply), flush=True)
