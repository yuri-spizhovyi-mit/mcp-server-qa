"""Run the server: `python -m energy_mcp [--transport stdio|http] [--host H] [--port P] [--seed N] ...`.

stdio is the default (what Claude Code and the desktop app launch). Its caller is read from
the environment (AUTH-D3): `ENERGY_MCP_SCOPES` (space-separated, default read-only) and
`ENERGY_MCP_OPERATOR`. On stdio, stdout belongs to the protocol: nothing else may print there
(the SDK logs to stderr).

HTTP binds to 127.0.0.1 unless told otherwise and serves Streamable HTTP at /mcp behind the
auth edge. It needs the token issuer's public key and name (AUTH-D6); there is no
unauthenticated HTTP mode.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import uvicorn

from energy_mcp.access import identity_from_env
from energy_mcp.edge import build_http_app
from energy_mcp.server import build_server
from energy_mcp.stdio import run_stdio
from energy_mcp.tokens import TokenSettings
from energy_mcp.variants import VARIANTS
from energy_mcp.world import build_world


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="energy-mcp", description="The energy_mcp MCP server (synthetic data).")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP only (default: 8000)")
    parser.add_argument("--seed", type=int, default=42, help="seed of the synthetic world (default: 42)")
    parser.add_argument("--public-key", help="HTTP only, required: PEM file with the token issuer's public key")
    parser.add_argument("--issuer", help="HTTP only, required: the token issuer (the tokens' iss)")
    parser.add_argument("--resource", help="HTTP only: this server's URL, also the tokens' audience "
                                           "(default: http://HOST:PORT/mcp)")
    parser.add_argument("--rate-limit", type=int, default=60, help="HTTP only: requests per window per caller")
    parser.add_argument("--rate-window", type=float, default=60.0, help="HTTP only: window in seconds")
    parser.add_argument("--variant", choices=sorted(VARIANTS), help=argparse.SUPPRESS)  # broken copies, tests only
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.transport == "http" and not (args.public_key and args.issuer):
        parser.error("HTTP needs --public-key and --issuer (there is no unauthenticated HTTP mode)")
    if args.rate_limit < 1 or args.rate_window <= 0:
        parser.error("--rate-limit must be at least 1 and --rate-window above 0")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    world = build_world(args.seed)
    variant = VARIANTS[args.variant] if args.variant else None
    if variant is not None:
        print(f"energy_mcp: running broken copy {args.variant}: {variant.description}", file=sys.stderr)
    if args.transport == "stdio":
        try:
            identity = identity_from_env(os.environ)
        except ValueError as error:
            _parser().error(str(error))
        if variant is None:
            run_stdio(build_server(world, identity=identity))
        else:
            if variant.startup_stdout is not None:  # before the stdio transport claims stdout
                print(variant.startup_stdout, flush=True)
            run_stdio(build_server(world, identity=identity, **variant.server_options()),
                      outgoing=variant.outgoing, answer_malformed=variant.answer_malformed)
        return
    with open(args.public_key, "rb") as key:
        settings = TokenSettings(issuer=args.issuer, audience=args.resource or f"http://{args.host}:{args.port}/mcp",
                                 public_key_pem=key.read())
    app, _ = build_http_app(world, settings=settings, clock=time.time, rate_limit=args.rate_limit,
                            rate_window=args.rate_window, host=args.host)
    if variant is not None and variant.asgi is not None:
        app = variant.asgi(app)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
