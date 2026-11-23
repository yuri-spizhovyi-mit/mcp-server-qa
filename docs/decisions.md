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
- Decision: over HTTP, a 403 before the SDK sees the call, with
  `WWW-Authenticate: Bearer error="insufficient_scope", scope="<the scopes it needs>",
  resource_metadata="<metadata URL>", error_description="This call needs more scope"`.
- Why: the 2026-07-28 spec says servers SHOULD answer a runtime scope shortfall this way,
  and clients treat the challenge's `scope` as authoritative for a step-up. Refusing before
  the SDK means a refused call can never reach a tool. A tool error would let the model read
  it, but no client could use it to ask for more scope.
- Held by: `tests/test_http_auth.py::test_each_call_needs_its_scope`,
  `tests/test_http_auth.py::test_refused_calls_leave_the_world_unchanged`,
  `tests/test_http_auth.py::test_challenge_never_carries_caller_text`

## AUTH-D2: What does a rate-limited call return?
- Decision: over HTTP, 429 with `Retry-After: <seconds, rounded up>` and a JSON body
  `{"error": "rate_limited", ...}`. The count is per principal `(iss, sub, client_id)`, not
  per token. Every authenticated request to `/mcp` counts, refused ones included. It is a
  fixed window (default 60 requests per 60 s) on the server's injected clock. stdio is not
  limited: it has one local user and no token.
- Why: the spec says nothing about rate limiting; 429 with `Retry-After` is the HTTP
  standard and tells a client exactly when to retry. Counting per principal means minting a
  fresh token doesn't reset the window. Unauthenticated requests (401) are not counted; that
  flood case belongs to M5's rate-limit abuse tests.
- Held by: `tests/test_rate_limit.py::test_call_n_plus_one_is_429_and_resets_on_the_clock`,
  `tests/test_rate_limit.py::test_new_token_same_principal_stays_limited`,
  `tests/test_rate_limit.py::test_unauthenticated_requests_are_not_counted`

## AUTH-D3: Who is the caller without HTTP, and how is a missing scope refused there?
- Decision: stdio (and the in-process server) runs as `Identity(operator, scopes)` from
  `ENERGY_MCP_OPERATOR` (default `local-operator`) and `ENERGY_MCP_SCOPES` (space-separated,
  default `meters:read alerts:read reports:read`, so read-only). An unknown scope or a
  malformed operator stops the server at startup. A refused tool call is a tool error
  (`isError`, `Not allowed: this call needs scope <s>`); a refused resource read or prompt
  is `-32602` with the same text. The same check (`ScopeGuard`) runs behind the HTTP edge
  too, as a second line.
- Why: the spec says stdio SHOULD take credentials from the environment. Read-only by
  default means a server started by a desktop client can't write until someone says so. A
  typo in a scope name should stop the server, not silently grant less. A tool error is what
  the model can read; there is no HTTP status on stdio.
- Held by: `tests/test_scopes.py::test_identity_from_env`,
  `tests/test_scopes.py::test_guard_refuses_without_scope_in_process`,
  `tests/test_cli_auth.py::test_stdio_process_is_read_only_by_default`

## AUTH-D4: Which scopes exist, and which scope implies which?
- Decision: `meters:read` (`list_meters`, `get_readings`), `alerts:read` (`list_alerts`, the
  `investigate_alert` prompt), `reports:read` (`resources/read`), `alerts:write` or
  `alerts:write:<alert_id>` (`acknowledge_alert`). Listing methods and everything else need
  only a valid token. `alerts:write` implies every `alerts:write:ALR-NNNN`; nothing else
  implies anything. A missing or malformed `alert_id` needs `alerts:write`.
- Why: the 2026-07-28 spec says servers MUST account for scope hierarchies. Per-alert scopes
  are how a confused deputy is stopped in code (plan, M5). Deriving the per-alert scope only
  from an ID that matches `ALR-[0-9]{4}` keeps caller text out of the challenge header.
- Held by: `tests/test_scopes.py::test_needed_scopes_per_method`, `tests/test_scopes.py::test_hierarchy`,
  `tests/test_http_auth.py::test_per_alert_token_over_http`

## AUTH-D5: Which tokens are accepted?
- Decision: RS256 only, signed by the configured public key, header `typ` `at+jwt` or
  `application/at+jwt` (RFC 9068 §4). It must have `iss`, `aud`, `exp`, `iat`, `sub`,
  `client_id`, `jti` and a string `scope` (RFC 9068 §2.2). Issuer and audience match
  exactly. On the injected clock with no leeway: `exp > now`, `iat <= now`, `nbf <= now`
  when present. `sub` and `client_id` must fullmatch `[A-Za-z0-9._-]{1,64}`.
- Why: one fixed algorithm closes `alg: none` and the HS256-with-public-key confusion. `typ`
  stops an ID token being replayed as an access token. `sub` becomes `acknowledged_by`, which
  other callers' models read, so it is held to a plain format. No leeway because the clock is
  ours.
- Held by: `tests/test_tokens.py::test_bad_tokens_are_rejected`,
  `tests/test_tokens.py::test_expiry_follows_the_injected_clock`

## AUTH-D6: Can the server run over HTTP without auth?
- Decision: no. `--transport http` refuses to start without `--public-key` and `--issuer`.
  A server built for HTTP falls back to an identity with no scopes, so if the edge were
  bypassed, `ScopeGuard` would refuse every call.
- Why: an unauthenticated mode that exists gets used. Failing closed is a guardrail in code,
  not a sentence in the README.
- Held by: `tests/test_cli_auth.py::test_http_needs_key_and_issuer`,
  `tests/test_http_auth.py::test_scope_guard_holds_if_the_edge_lets_a_call_through`

## AUTH-D7: What does the HTTP edge do with a request it can't read for certain?
- Decision: the edge decides on every request itself and hands the SDK only what it read for
  certain.
  - A POST body that is not one UTF-8 JSON object without duplicate keys gets 400 with a
    JSON-RPC `-32700` body. Arrays, invalid JSON, non-UTF-8 and duplicate keys at any depth
    are all refused. The content type is ignored: the body is read whatever it claims.
  - A body over `max_body` (default 4 MiB) gets 413.
  - A client that hangs up before the body is complete gets nothing, and nothing is handed
    on.
  - The order is 401, then 429, then 400, then 403, so an unauthenticated caller learns
    nothing about the body.
  - Every path except the RFC 9728 metadata needs a token, not only `/mcp`.
  - `Retry-After` is at least 1.
- Why: the scope check is only as good as the edge's reading of the call. If the edge and
  the SDK could read one body differently (which duplicate key wins, a truncated body
  replayed as complete), a write could pass the edge's check. Today the SDK keeps the last
  duplicate key, as Python does, and rejects batches itself. The edge doesn't rely on
  either. Guarding every path means a trailing slash or a future route can't sit outside
  the edge. The plan first said unreadable bodies would be "handed to the SDK"; this is
  stricter, made during M2 and recorded in the plan.
- Held by: `tests/test_http_auth.py::test_unreadable_bodies_cannot_write`,
  `tests/test_http_auth.py::test_a_complete_raw_request_gets_through`,
  `tests/test_http_auth.py::test_content_type_does_not_hide_a_call`,
  `tests/test_http_auth.py::test_body_over_the_limit_is_refused`,
  `tests/test_http_auth.py::test_aborted_request_is_not_executed`,
  `tests/test_http_auth.py::test_every_path_but_metadata_needs_a_token`,
  `tests/test_rate_limit.py::test_call_n_plus_one_is_429_and_resets_on_the_clock`

## SRV-D1: What does a call with arguments that break the tool's schema return?
- Decision: a tool execution error (`isError: true`), as the spec lists input validation
  errors. The text is `Invalid arguments for tool <name>: <path>: <rule>; ...`. It names the
  field and the broken rule and never repeats anything the caller chose: no values, no
  unknown keys, no unknown tool, prompt, argument or report names. Where a name is wrong,
  the error lists the server's own allowed names instead (`unexpected property; allowed:
  cursor, limit, meter_id`). An unknown tool is a protocol error, `-32602`, with the list of
  available tools. IDs are echoed only after they match their ASCII pattern
  (`Unknown alert ALR-0999`).
- Why: the model can read and correct a tool error (the spec says clients SHOULD pass them
  on), while echoing caller text back, a value or a key, would hand injected text straight to
  the next model turn (F-05). The spec's own example echoes the unknown tool name; we list the
  available tools instead, which helps the model at least as much.
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
