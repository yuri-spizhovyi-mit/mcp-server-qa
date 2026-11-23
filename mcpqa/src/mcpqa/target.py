"""The server under test, from `--target` (PROTO-D3) and `--token-env`."""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

USAGE = ('use stdio:["python", "-m", "server"] (exact, for Windows paths), stdio:python -m server, '
         "or http://host:port/mcp")


@dataclass(frozen=True)
class Target:
    kind: Literal["stdio", "http"]
    argv: tuple[str, ...] = ()
    url: str = ""
    token: str | None = None


def _bad(reason: str) -> ValueError:
    return ValueError(f"bad --target: {reason}; {USAGE}")


def parse_target(text: str, *, token_env: str | None = None, env: Mapping[str, str] = os.environ) -> Target:
    token = None
    if token_env:
        if token_env not in env:  # name the variable, never a value
            raise ValueError(f"--token-env names {token_env}, which is not set in the environment")
        token = env[token_env]
    if text.startswith("stdio:"):
        rest = text[len("stdio:"):].strip()
        if rest.startswith("["):
            try:
                argv = json.loads(rest)
            except ValueError:
                raise _bad("the stdio command is not a JSON array") from None
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                raise _bad("the stdio JSON array must hold non-empty strings")
        else:
            argv = shlex.split(rest)
        if not argv:
            raise _bad("no stdio command")
        return Target("stdio", tuple(argv), token=token)
    if text.startswith(("http://", "https://")):
        if not urlsplit(text).netloc:
            raise _bad("the URL has no host")
        return Target("http", url=text, token=token)
    raise _bad("unknown form")
