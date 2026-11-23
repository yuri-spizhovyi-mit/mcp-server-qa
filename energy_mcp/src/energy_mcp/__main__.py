"""Run the server: `python -m energy_mcp [--transport stdio|http] [--host H] [--port P] [--seed N]`.

stdio is the default (what Claude Code and the desktop app launch). HTTP binds to 127.0.0.1
unless told otherwise, and serves Streamable HTTP at /mcp. On stdio, stdout belongs to the
protocol: nothing else may print there (the SDK logs to stderr).
"""

from __future__ import annotations

import argparse

from energy_mcp.server import build_server
from energy_mcp.world import build_world


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="energy-mcp", description="The energy_mcp MCP server (synthetic data).")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP only (default: 8000)")
    parser.add_argument("--seed", type=int, default=42, help="seed of the synthetic world (default: 42)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    server = build_server(build_world(args.seed))
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
