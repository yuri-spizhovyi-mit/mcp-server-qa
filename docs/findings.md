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
- Status: open in the SDK; worked around in `energy_mcp` (M1, Task 4).

## F-02: The SDK accepts arguments that its own published schema forbids
- Found by: the M1 SDK spike; pinned by
  `tests/test_sdk_findings.py::test_f02_bare_sdk_coerces_and_drops_arguments`
- Evidence: for a tool `count(n: int)`, whose `inputSchema` says `"n": {"type": "integer"}`,
  the bare server accepts `{"n": "2"}` and `{"n": true}` (pydantic lax mode) and silently drops
  `{"zzz": 1}`. The spec (server/tools, "Security Considerations"): "Servers MUST: Validate all
  tool inputs". A client that trusts the schema and one that doesn't get different behaviour.
- Status: open in the SDK; worked around in `energy_mcp` (M1, Task 4).

## F-03: The SDK answers a missing prompt argument with code 0 or -32603
- Found by: the M1 SDK spike; pinned by
  `tests/test_sdk_findings.py::test_f03_bare_sdk_missing_prompt_argument_is_not_32602`
- Evidence: `prompts/get` without a required argument returns error code `0` on 2025-11-25
  and `-32603` "Internal server error" on 2026-07-28. The spec (server/prompts, "Error
  Handling"): "Missing required arguments: `-32602` (Invalid params)".
- Status: open in the SDK; worked around in `energy_mcp` (M1, Task 5).

## F-04: Our plan stated two SDK facts that were wrong (a harness bug)
- Found by: the M1 SDK spike, before any M1 code was written.
- Evidence: `docs/plan.md` said the SDK "does not validate tool arguments against
  `inputSchema`" and "a handler exception becomes a `-32603` protocol error". In mcp 2.3.0,
  `MCPServer` validates arguments with pydantic (laxly, F-02) and turns any exception in a
  tool into an `isError` result whose text is "Error executing tool <name>"
  (`mcp/server/mcpserver/tools/base.py`, `Tool.run`). The lowlevel `Server` is the one that
  doesn't validate; the plan had mixed up the two.
- Status: fixed in the M1 PR (plan corrected; SRV-D4 decides what a crash returns).
