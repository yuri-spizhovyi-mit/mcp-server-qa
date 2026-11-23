"""The argument guard: an SDK middleware that holds tool calls to the spec and to their schemas.

It runs before the SDK's own handling, on every transport and revision, and fixes three SDK
defaults (docs/findings.md):
- F-01: an unknown tool is the protocol error -32602, not an `isError` result;
- F-02: arguments are validated strictly against the published `inputSchema` (Draft 2020-12),
  so "2" is not an integer and an unknown argument is rejected (SRV-D2);
- F-03: an unknown prompt, or a missing or unexpected prompt argument, is -32602.
A schema violation is a tool execution error (`isError`) that names the field and the rule
and never repeats what the caller sent: no values, no unknown keys or names (SRV-D1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from jsonschema import Draft202012Validator, ValidationError, validators
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent
from mcp_types.jsonrpc import INVALID_PARAMS

NULL = {"type": "null"}


def _ecma(pattern: str) -> re.Pattern[str]:
    r"""A JSON Schema pattern as Python regex with ECMA-262 behaviour for the patterns we publish.

    Python's `$` also matches before a final newline and its `\d` matches any Unicode digit;
    in ECMA-262 neither does. Without this, "MTR-0001\n" passes the guard, pydantic then
    rejects it, and the SDK's own error text (which echoes the value) reaches the model.
    A final unescaped `$` becomes `\Z`, and matching is ASCII-only.
    """
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern, re.ASCII)


def _pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string") and not _ecma(pattern).search(instance):
        yield ValidationError(f"does not match {pattern!r}")


StrictValidator = validators.extend(Draft202012Validator, {"pattern": _pattern})


def _parts(error: ValidationError) -> list[str]:
    """`<path>: <what>` for one jsonschema error, without the offending value."""
    path = ".".join(str(p) for p in error.absolute_path) or "(root)"
    if error.validator == "required":
        return [f"{path}: missing required property {name!r}"
                for name in error.validator_value if name not in error.instance]
    if error.validator == "additionalProperties":
        # The extra key is the caller's text, so it is not repeated; the allowed names are.
        allowed = ", ".join(sorted(error.schema.get("properties", {})))
        return [f"{path}: unexpected property; allowed: {allowed}"]
    if error.validator == "anyOf":
        # pydantic publishes an optional parameter as anyOf [X, {"type": "null"}]: report X's error.
        inner = [e for e in error.context or [] if e.schema != NULL]
        if inner:
            return [part for e in inner for part in _parts(e)]
    return [f"{path}: must satisfy {error.validator} {json.dumps(error.validator_value)}"]


class ArgumentGuard:
    def __init__(self, tool_schemas: Mapping[str, Mapping[str, Any]],
                 prompt_args: Mapping[str, tuple[frozenset[str], frozenset[str]]] | None = None) -> None:
        self._validators = {name: StrictValidator(dict(schema)) for name, schema in tool_schemas.items()}
        # prompt name -> (required argument names, allowed argument names)
        self._prompt_args = dict(prompt_args or {})

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        params = ctx.params if isinstance(ctx.params, Mapping) else {}
        if ctx.method == "tools/call" and isinstance(params.get("name"), str):
            rejected = self._check_tool(params["name"], params.get("arguments"))
            if rejected is not None:
                return rejected
        elif ctx.method == "prompts/get" and isinstance(params.get("name"), str):
            self._check_prompt(params["name"], params.get("arguments"))
        return await call_next(ctx)

    def _check_tool(self, name: str, arguments: Any) -> CallToolResult | None:
        validator = self._validators.get(name)
        if validator is None:
            available = ", ".join(sorted(self._validators))  # the caller's name is not echoed (SRV-D1)
            raise MCPError(code=INVALID_PARAMS, message=f"Unknown tool; available: {available}")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, Mapping):
            raise MCPError(code=INVALID_PARAMS, message="Invalid params: arguments must be an object")
        parts = sorted({part for error in validator.iter_errors(dict(arguments)) for part in _parts(error)})
        if not parts:
            return None
        text = f"Invalid arguments for tool {name}: " + "; ".join(parts)
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)

    def _check_prompt(self, name: str, arguments: Any) -> None:
        """F-03: every bad prompts/get is -32602 (the SDK answers code 0 or -32603)."""
        if name not in self._prompt_args:
            available = ", ".join(sorted(self._prompt_args))
            raise MCPError(code=INVALID_PARAMS, message=f"Unknown prompt; available: {available}")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, Mapping):
            raise MCPError(code=INVALID_PARAMS, message="Invalid params: arguments must be an object")
        required, allowed = self._prompt_args[name]
        if missing := sorted(required - set(arguments)):
            raise MCPError(code=INVALID_PARAMS,
                           message=f"Missing required argument for prompt {name}: {', '.join(missing)}")
        if set(arguments) - allowed:
            raise MCPError(code=INVALID_PARAMS,
                           message=f"Unexpected argument for prompt {name}; allowed: {', '.join(sorted(allowed))}")
