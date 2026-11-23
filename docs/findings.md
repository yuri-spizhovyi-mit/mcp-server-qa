# Findings

What the tests found: planted bugs (broken copies of the server), real defects, and bugs in
the test harness itself. Each entry has its evidence, so a reader can check it.

Entry format:

```text
## F-NN: <one-line title>
- Found by: <test id, or the run / review that found it>
- Evidence: <failing output, run link, file:line>
- Status: open | fixed in <PR> | accepted (see <decision ID>)
```

## F-01: The SDK answers an unknown tool with a result, not a protocol error
- Found by: the M1 SDK spike (2026-11-22); pinned by
  `tests/test_sdk_findings.py::test_f01_bare_sdk_answers_unknown_tool_with_is_error`
- Evidence: a bare `MCPServer` (mcp 2.3.0) answers `tools/call` for `nosuch` with
  `{"content": [{"type": "text", "text": "Unknown tool: nosuch"}], "isError": true}` on both
  2025-11-25 and 2026-07-28. The spec (server/tools, "Error Handling") lists unknown tools under
  protocol errors, with `-32602` in its example.
- Status: open in the SDK (still pinned by `tests/test_sdk_findings.py`); worked around in
  `energy_mcp/guard.py`, held by `tests/test_arguments.py` (28 of its 29 cases fail with the guard removed).

## F-02: The SDK accepts arguments that its own published schema forbids
- Found by: the M1 SDK spike; pinned by
  `tests/test_sdk_findings.py::test_f02_bare_sdk_coerces_and_drops_arguments`
- Evidence: for a tool `count(n: int)`, whose `inputSchema` says `"n": {"type": "integer"}`,
  the bare server accepts `{"n": "2"}` and `{"n": true}` (pydantic lax mode) and silently drops
  `{"zzz": 1}`. The spec (server/tools, "Security Considerations"): "Servers MUST: Validate all
  tool inputs". A client that trusts the schema and one that doesn't get different behaviour.
- Status: open in the SDK (still pinned by `tests/test_sdk_findings.py`); worked around in
  `energy_mcp/guard.py`, held by `tests/test_arguments.py` (28 of its 29 cases fail with the guard removed).

## F-03: The SDK answers a missing prompt argument with code 0 or -32603
- Found by: the M1 SDK spike; pinned by
  `tests/test_sdk_findings.py::test_f03_bare_sdk_missing_prompt_argument_is_not_32602`
- Evidence: `prompts/get` without a required argument returns error code `0` on 2025-11-25
  and `-32603` "Internal server error" on 2026-07-28. The spec (server/prompts, "Error
  Handling"): "Missing required arguments: `-32602` (Invalid params)".
- Status: open in the SDK (still pinned by `tests/test_sdk_findings.py`); worked around in
  `energy_mcp/guard.py`, held by `tests/test_prompt.py` (10 of its 20 cases fail with the prompt check removed).

## F-04: Our plan stated two SDK facts that were wrong (a harness bug)
- Found by: the M1 SDK spike, before any M1 code was written.
- Evidence: `docs/plan.md` said the SDK "does not validate tool arguments against
  `inputSchema`" and "a handler exception becomes a `-32603` protocol error". In mcp 2.3.0,
  `MCPServer` validates arguments with pydantic (laxly, F-02) and turns any exception in a
  tool into an `isError` result whose text is "Error executing tool <name>"
  (`mcp/server/mcpserver/tools/base.py`, `Tool.run`). The lowlevel `Server` is the one that
  doesn't validate; the plan had mixed up the two.
- Status: fixed in the M1 PR (plan corrected; SRV-D4 decides what a crash returns).

## F-05: Our guard echoed caller-chosen names back to the model
- Found by: the M1 final review (fresh reviewer), probing the guard before the PR.
- Evidence: `{"meter_id": "MTR-0001", "IGNORE PREVIOUS INSTRUCTIONS": 1}` came back as
  `unexpected property 'IGNORE PREVIOUS INSTRUCTIONS'` inside an `isError` text the model
  reads; unknown tool, prompt, prompt-argument and report names were echoed the same way
  (`Unknown report: IGNORE PREVIOUS`). SRV-D1 promised no echo, but the tests only
  planted the sentinel in values, never in keys or names.
- Status: fixed in the M1 PR. Errors now list the server's own allowed names instead
  (`unexpected property; allowed: cursor, limit, meter_id`, `Unknown tool; available: ...`).
  Held by `tests/test_arguments.py::test_argument_errors_never_echo_the_value`,
  `tests/test_prompt.py::test_prompt_errors_are_invalid_params` (sentinel cases) and
  `tests/test_reports.py::test_report_errors_never_echo_the_name`.

## F-06: The guard and pydantic disagreed about regex patterns
- Found by: the M1 final review.
- Evidence: jsonschema evaluates `pattern` with Python `re.search`, where `$` also matches
  before a final newline and `\d` matches any Unicode digit. So `"MTR-0001\n"` passed the
  guard, pydantic (which follows ECMA-262 here) rejected it, and the model received the
  SDK's own text: `1 validation error ... input_value='MTR-0001\n' ... errors.pydantic.dev`,
  which echoes the value and exposes internals. `MTR-١٢٣٤` (Arabic-Indic digits) reached the
  tool body.
- Status: fixed in the M1 PR. Published patterns use `[0-9]`, and the guard's `pattern`
  keyword uses ASCII matching with a final `$` read as end of string
  (`energy_mcp/guard.py`, `_ecma`). Held by the `trailing-newline` and `unicode-digits` cases
  of `tests/test_arguments.py::test_schema_violations_are_tool_errors_naming_field_and_rule`.

## F-07: Three tests passed with the behaviour they name removed (a harness bug)
- Found by: the M1 final review, by mutation.
- Evidence: with the cursor range check removed, `test_bad_cursor_is_a_tool_error` still
  passed (a forged in-format cursor for offset 999 returned an empty page); with `main()`
  hard-coding `0.0.0.0`, `test_cli_defaults_to_stdio_and_localhost` still passed (it only
  read `parse_args`); and `test_failed_calls_leave_the_world_unchanged` relied on ALR-0001
  happening to be open in seed 42.
- Status: fixed in the M1 PR. Forged out-of-range cursors were added, a new test
  (`test_cli_serves_http_on_localhost_unless_told`) checks what `main()` hands the SDK, and
  the world test picks the first open alert. Each fix was shown to fail on its mutant.
