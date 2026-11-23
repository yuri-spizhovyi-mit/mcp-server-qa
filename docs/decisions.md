# Decisions

Behaviour choices that the spec or the plan leaves open, each with the test that holds it.
IDs are `<AREA>-Dn` (areas as in `docs/plan.md`).

Entry format:

```text
## <AREA>-Dn: <the question>
- Decision: <what we chose> (or "Open: decided in Mn")
- Why: <the reason, and what the alternative would cost>
- Held by: <test id>
```

## ENV-D1: How exactly are dependencies pinned?
- Decision: `requirements.txt` pins every direct dependency exactly (`name==version`) and
  installs both packages editable. Each package's `pyproject.toml` declares ranges
  (`mcp>=2.3,<3`).
- Why: the repo and CI must resolve the same versions on every run, while `mcpqa` must stay
  installable next to someone else's server, which exact pins in its metadata would block.
  Transitive dependencies are not locked; if one breaks a run, that is a finding and a lock
  file becomes a new decision.
- Held by: `tests/test_packages.py::test_installed_versions_match_pins`

## AUTH-D1: Does a call without the needed scope fail at HTTP level or as a tool error?
- Decision: Open: decided in M2. Starting position: HTTP 403 with
  `WWW-Authenticate: Bearer error="insufficient_scope"`, as the 2026-07-28 spec suggests.
- Held by: (M2)

## AUTH-D2: What does a rate-limited call return?
- Decision: Open: decided in M2 (HTTP 429 vs a JSON-RPC error, and on stdio).
- Held by: (M2)

## SRV-D1: What does a call with arguments that break the tool's schema return?
- Decision: a tool execution error (`isError: true`), as the spec lists input validation
  errors. The text is `Invalid arguments for tool <name>: <path>: <rule>; ...`. It names the
  field and the broken rule and never repeats the value the caller sent. An unknown tool is a
  protocol error, `-32602`.
- Why: the model can read and correct a tool error (the spec says clients SHOULD pass them
  on), while echoing a value back would hand injected text straight to the next model turn.
- Held by: `tests/test_arguments.py::test_schema_violations_are_tool_errors_naming_field_and_rule`,
  `tests/test_arguments.py::test_argument_errors_never_echo_the_value`,
  `tests/test_arguments.py::test_unknown_tool_is_invalid_params`

## SRV-D2: Are unknown arguments ignored or rejected?
- Decision: rejected, and every tool's published `inputSchema` says
  `"additionalProperties": false`.
- Why: a silently dropped argument (a typo such as `meterid`) gives the caller a wrong answer
  that looks right. The spec is silent; rejecting is the stricter contract, and a schema that
  says so lets `mcpqa` fuzz it (M4).
- Held by: `tests/test_tools_contract.py::test_tools_listed_with_object_schemas_that_forbid_extras`,
  `tests/test_arguments.py::test_schema_violations_are_tool_errors_naming_field_and_rule`

## SRV-D3: What does acknowledging an already-acknowledged alert do?
- Decision: nothing new. It returns the first acknowledgement unchanged (who and when), and
  the tool is annotated `idempotentHint: true`, `destructiveHint: false`.
- Why: agents retry. A retry that overwrote who acknowledged the alert and when would
  rewrite history; a retry that failed would make the agent think the first call failed.
- Held by: `tests/test_world.py::test_acknowledge_is_idempotent_and_unknown_raises`,
  `tests/test_tools_behaviour.py::test_acknowledge_changes_the_world_and_is_idempotent`

## SRV-D4: What does a tool that crashes return?
- Decision: the JSON-RPC error `-32603` "Internal error", with no detail; the exception is
  logged on the server. This overrides the SDK's default (an `isError` result).
- Why: the spec lists server errors under protocol errors. A crash that looks like a normal
  tool error can't be told apart from a clean refusal, so fuzzing (M4) would miss it.
  Internals (paths, stack traces) stay on the server.
- Held by: `tests/test_tools_behaviour.py::test_tool_crash_is_internal_error`
