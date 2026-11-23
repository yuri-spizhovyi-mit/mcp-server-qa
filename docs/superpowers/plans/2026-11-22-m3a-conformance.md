# M3a Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mcpqa` becomes a pytest plugin that checks any MCP server's protocol conformance on both revisions, over stdio or Streamable HTTP. It speaks the protocol with its own raw client. `energy_mcp` passes every check, and each broken copy of `energy_mcp` fails exactly the checks it should. This is part a of milestone 3. Part b (cancellation, timeouts, the official test server in CI, the TypeScript client) gets its own plan and PR.

**Architecture:** approach A, approved by the owner on 2026-11-22. `mcpqa` never imports the MCP SDK.
- `target.py` parses `--target`.
- `wire.py` holds the raw transports: JSON lines over a subprocess's stdio, and plain POSTs over HTTP with SSE replies parsed.
- `protocol.py` adds what each revision needs (the 2026-07-28 envelope and headers, or the legacy `initialize` handshake and session header).
- `plugin.py` is the pytest plugin: options, fixtures, the "not applicable" rule.
- The checks are ordinary pytest tests in `mcpqa/conformance/`, run with `pytest --pyargs mcpqa.conformance --target ...`.

On the `energy_mcp` side:
- A stdio front answers malformed lines (F-13), and the guard rejects list cursors (F-14).
- `variants.py` holds the broken copies, selected with `--variant NAME`.
- The repo's own tests run `mcpqa` through `pytester` against the clean server and each broken copy.

**Tech Stack:** Python 3.12, mcp 2.3.0 (for `energy_mcp` only), httpx2 2.16.2 (`mcpqa`'s HTTP client; now pinned directly), pytest 9.2.0 with `pytester`, anyio.

**Spec:** [`docs/plan.md`](../../plan.md), milestone 3, plus [`CLAUDE.md`](../../../CLAUDE.md), plus the owner-approved calls below.
- The spec rules come from the MCP pages for 2026-07-28 and 2025-11-25 (base protocol, versioning, lifecycle, transports, Streamable HTTP, stdio, cancellation, pagination, `server/discover`, schema errors), read on 2026-11-22.
- The SDK facts come from throwaway spikes on the installed mcp 2.3.0, the same day.

## What the spec and the SDK say (checked, not guessed)

| Case | Spec | `energy_mcp` today (mcp 2.3.0) | M3a |
|---|---|---|---|
| Invalid JSON | ParseError `-32700`; JSON-RPC 2.0: reply with `id: null` | stdio: **no answer** (F-13). HTTP: our edge's 400 `-32700` | the stdio front answers `-32700` |
| Valid JSON, not a request (`{"jsonrpc":"2.0","id":2}`) | InvalidRequest `-32600` | stdio: **no answer** (F-13). HTTP: 400 `-32600` | the stdio front answers `-32600` with the detected `id`, else `null` |
| Unknown method | MethodNotFound `-32601`; over 2026 HTTP, MUST 404 | `-32601`; HTTP 404 | check |
| Unknown tool | `-32602` | `-32602` (M1 guard) | check |
| 2026: unsupported version | MUST `-32022`, `data: {supported, requested}`, HTTP 400 | `-32022`, `supported: ["2026-07-28"]`, HTTP 400 | check (PROTO-D1) |
| 2026: no `_meta` envelope | MUST `-32602`, HTTP 400 | `-32602`, 400 | check |
| 2026: header doesn't match body | `-32020`, HTTP 400 | `-32020`, 400 | check (HTTP only) |
| 2026: `server/discover` | servers MUST implement; `resultType`, `supportedVersions`, `capabilities` | yes | check |
| 2025: `initialize` with an unknown version | server MUST answer with another version it supports | answers `2025-11-25` | check (PROTO-D2) |
| Invalid cursor on a list method | SHOULD `-32602` | **ignored, first page** on `tools/list` and `resources/list` (F-14) | the guard rejects any list cursor (we never issue one) |
| Capabilities | a method gated by an unadvertised capability → `-32601` | declares tools, resources, prompts | check: declared works, undeclared is `-32601` |
| stdout on stdio | MUST carry only MCP messages | yes | check |
| Message size | no limit in the spec | a 1 MiB argument gets a clean `isError` | check: answered cleanly, server still answers |
| One stdio connection, two revisions | | once a revision is used, the other is refused (`-32022`) | `mcpqa` opens one process per revision |
| Middleware visibility | | sees unknown methods, `initialize` and notifications; not requests with a bad version or no envelope (refused earlier) | broken copies use middleware, or rewrite outgoing messages in the stdio front |
| TypeScript SDK | | the plan names `@modelcontextprotocol/sdk` 1.32.2; the current line is `@modelcontextprotocol/client` 2.4.0, which `server-everything` 2.3.1 is built on | F-15; plan corrected (used in M3b) |

## Owner-approved calls (2026-11-22)

- **Approach A:** a raw client of our own; `mcpqa` doesn't import `mcp`.
- **M3 lands in two PRs.**
  - M3a is this plan.
  - M3b covers cancellation and timeouts, with a slow read-only `check_meter(meter_id)` tool added to `energy_mcp` that reports progress. It also covers `server-everything` 2.3.1 in CI and the interop check with `@modelcontextprotocol/client` 2.4.0.
- **Broken copies:**
  - Selected with `energy_mcp --variant NAME`.
  - The repo's tests require exactly the named checks to fail; every other check must pass.
  - They run over stdio, except an HTTP-only check's copy, which runs over HTTP.
- **HTTP auth for `mcpqa`:** `--token-env VAR` names the environment variable holding a bearer token. The token never appears on the command line or in reports.
- **Read-only by default:** `mcpqa` calls only tools annotated `readOnlyHint: true`, unless given `--allow-writes`. A check whose precondition is missing skips with a reason that starts `not applicable:`.

My calls within that, recorded as decisions in Task 1:
- **PROTO-D1:** the `-32022` check requires all of these:
  - code `-32022`;
  - `data.requested` equal to what was sent;
  - `data.supported` a non-empty list that contains `2026-07-28` and no version the server refuses;
  - HTTP 400.

  It does not require `2025-11-25` in the list. The SDK lists only the revision of the request's style. The spec's example lists both, but its text says only "the versions it does support".
- **PROTO-D2:** the legacy negotiation check requires a *result* with a `protocolVersion` the server actually serves; an error fails. The spec says MUST.
- **PROTO-D3:** the `--target` format. Either `stdio:` plus a JSON array (exact argv; Windows paths need this form), or `stdio:` plus a command split with `shlex` (POSIX rules), or an `http://` / `https://` URL.
- **PROTO-D4:** a malformed stdio line gets an answer:
  - `-32700` for invalid JSON;
  - `-32600` for anything else that isn't a valid message, with the message's `id` when it is an integer or string, else `null`.

## Global Constraints

- Python 3.12; `mcp==2.3.0`.
  - Add `httpx2==2.16.2` to `requirements.txt` (imported directly by `mcpqa`; ENV-D1).
  - `mcpqa/pyproject.toml` gets `httpx2>=2.16,<3`, `pytest>=9,<10`, and the entry point `[project.entry-points.pytest11] mcpqa = "mcpqa.plugin"`.
  - After changing entry points, re-run `.\.venv\Scripts\python -m pip install -r requirements.txt`.
- `mcpqa` never imports `energy_mcp` (ENV-02) and, by approach A, never imports `mcp` or `mcp_types`. Task 3 adds a test for the second rule next to ENV-02's.
- Every repo test carries `req` (prefix `PROTO`). Rows go into `docs/requirements.csv` in the task that adds their tests. Run `python scripts/rtm.py` before each commit, and gate each commit on pytest's own exit code (the M2 lesson).
- `mcpqa` checks are not repo tests: `rtm.py` scans only `tests/`, so they need no `req`. Each check's name is its stable ID.
- **Determinism:**
  - Every wait has an upper bound: `--mcpqa-timeout`, default 10 s. The repo's tests pass 5.
  - Silence counts as a failure only after the bound.
  - Clean-server runs never wait on a bound, because every expected answer arrives.
- Commands run in **Windows PowerShell** from `C:\Users\yspizhoviy\mcp-server-qa`, using `.\.venv\Scripts\python`.
- Repo text files are LF. Write files with `newline="\n"`.

## Review Focus

1. **A check that can't fail** (the M2 lesson): every check has a broken copy that fails it, and `test_each_broken_copy_fails_exactly_its_checks` requires the failure. The clean run requires every check to pass or to be skipped with a listed reason.
2. **A check that fails for the wrong reason:** the broken-copy test compares the *set* of failed checks with the expected set, so a broken copy that also breaks a neighbour shows up.
3. **Process and stream leaks:** a stdio wire that doesn't kill its child on failure, or a reader thread blocked forever. `test_wire_closes_its_process_on_error` kills the process in the middle of a request.
4. **SSE replies over HTTP** (legacy answers can be `text/event-stream`), including several events and a `data:` line split across chunks: `test_sse_reply_is_parsed`.
5. **A target string with spaces or Windows backslashes:** the JSON-array form, and a clear error for the shell form that splits wrongly: `test_parse_target_forms`.

---

### Task 1: Plan corrections, findings, decisions, SDK pins

**Files:**
- Modify: `docs/plan.md`:
  - status line → "Milestones 0-2 are done; milestone 3 is in progress";
  - M2 status → "Done (PR #3)"; M3 status → "In progress (M3a, M3b)";
  - "MCP TypeScript SDK" row → `@modelcontextprotocol/client` 2.4.0, citing F-15;
  - M3 scope: note the split, and the `check_meter` tool in M3b.
- Modify: `docs/findings.md` (F-13, F-14, F-15), `docs/decisions.md` (PROTO-D1..D4), `docs/requirements.csv` (PROTO-09)
- Create: `tests/test_sdk_protocol_findings.py`, `tests/bare_stdio_server.py` (helper, not a test module: a bare `MCPServer` with one tool, run over stdio)

**Interfaces:**
- Produces requirement `PROTO-09` (high): "`energy_mcp` answers every stdio line that is not a valid message (`-32700` for invalid JSON, `-32600` otherwise, with the request's id when it has one) and rejects any cursor on a list method with `-32602`, where the bare SDK stays silent or ignores the cursor (F-13, F-14)."

- [ ] **Step 1: Write F-13, F-14, F-15 and PROTO-D1..D4.**
  - **F-13:** over stdio, mcp 2.3.0 sends a malformed line into the session as an exception and answers nothing. A client waits forever.
  - **F-14:** the list methods ignore a `cursor` they never issued and return the first page. The spec says invalid cursors SHOULD get `-32602`.
  - **F-15 (plan bug):** `docs/plan.md` named `@modelcontextprotocol/sdk` 1.32.2 as the TypeScript SDK. The current client is `@modelcontextprotocol/client` 2.4.0, and `server-everything` 2.3.1 is built on that line.
- [ ] **Step 2: Write `tests/test_sdk_protocol_findings.py`.** Each test is a characterization pin on the bare server:
  - `test_f13_bare_stdio_drops_malformed_lines` (PROTO-09): send `{"jsonrpc":"2.0","id":1,` and then a valid `2026-07-28` `tools/list` with id 2. The first answer read is for id 2.
  - `test_f14_bare_list_methods_ignore_a_cursor` (PROTO-09): in-process `Client(bare, mode="2026-07-28")`; `list_tools(cursor="mcpqa-invalid-cursor")` returns tools.
- [ ] **Step 3: Run them.**
  - Run: `.\.venv\Scripts\python -m pytest tests/test_sdk_protocol_findings.py -q -p no:cacheprovider`
  - Expected: `2 passed`. Then run `python scripts/rtm.py` and the full suite; expect everything to pass.
- [ ] **Step 4: Commit** `M3a: plan, F-13..F-15, PROTO decisions, SDK protocol pins`, including this plan file.

### Task 2: `energy_mcp` answers malformed stdio lines and rejects list cursors (PROTO-09)

**Files:**
- Create: `energy_mcp/src/energy_mcp/stdio.py`, `tests/test_stdio_front.py`
- Modify:
  - `energy_mcp/src/energy_mcp/guard.py`: list cursors.
  - `energy_mcp/src/energy_mcp/server.py`: `LIST_METHODS`.
  - `energy_mcp/src/energy_mcp/__main__.py`: run stdio through the front.
  - `tests/servers.py`: `RawStdio`, a sync helper for raw lines to a subprocess.

**Interfaces:**
- `run_stdio(server: MCPServer, *, outgoing: Callable[[dict], dict | None] | None = None, answer_malformed: bool = True) -> None`:
  - Runs the server over `mcp.server.stdio.stdio_server()`, putting its own streams between the SDK's and the server.
  - An `Exception` read from the SDK's stream is answered on the write stream: `-32700` when its pydantic error type is `json_invalid`, else `-32600`. The `id` comes from the error's `input` when that is a dict with an `int` or `str` `id`, else `null`.
  - `outgoing` (used by variants) may rewrite or drop each outgoing message as a dict.
- `ArgumentGuard(..., reject_list_cursors: bool = True)`: `tools/list`, `resources/list`, `resources/templates/list` and `prompts/list` with any non-null `cursor` raise `MCPError(INVALID_PARAMS, "Invalid cursor")`.
- Test helper `RawStdio(argv, env=None, timeout=5.0)`:
  - A context manager with `send(obj_or_bytes)`, `read() -> dict | None` (`None` after the timeout), and `noise: list[str]` for stdout lines that aren't JSON.
  - It kills the process on exit.

- [ ] **Step 1: Write `tests/test_stdio_front.py` (PROTO-09), failing.**
  - `test_malformed_lines_get_an_answer`, parametrized:

    | Line | Answer |
    |---|---|
    | `{"jsonrpc":"2.0","id":2,` | `(-32700, None)` |
    | `{"jsonrpc":"2.0","id":2}` | `(-32600, 2)` |
    | `[1,2]` | `(-32600, None)` |
    | `{"jsonrpc":"2.0","id":"x","method":5}` | `(-32600, "x")` |
    | `"\u00ff"` | `(-32600, None)` |

    Each line is sent to `python -m energy_mcp`; the answer read is `{"jsonrpc": "2.0", "id": <id>, "error": {"code": <code>, ...}}`. A valid request sent afterwards is still answered.
  - `test_list_cursor_is_invalid_params`, on both revisions (in-process `Client`): each list method with `cursor="mcpqa-invalid-cursor"` raises `MCPError` `-32602` "Invalid cursor".
- [ ] **Step 2: Run.**
  - Run: `.\.venv\Scripts\python -m pytest tests/test_stdio_front.py -q -p no:cacheprovider`
  - Expected: the malformed cases fail with `None` (no answer within 5 s); the cursor cases fail with no `MCPError` raised.
- [ ] **Step 3: Implement** `run_stdio`, the guard flag, and `main()` calling `run_stdio(build_server(...))`.
- [ ] **Step 4: Run the whole suite.** Expected: all pass, including SRV-07 and the M2 stdio tests.
- [ ] **Step 5: Mutation proof.** `answer_malformed=False` makes the five malformed cases fail; `reject_list_cursors=False` makes the cursor cases fail. Revert.
- [ ] **Step 6: Commit** `M3a: stdio front answers malformed lines, list cursors rejected (PROTO-09)`.

### Task 3: `mcpqa` core: target, wire, protocol, plugin (PROTO-01)

**Files:**
- Create in `mcpqa/src/mcpqa/`: `target.py`, `wire.py`, `protocol.py`, `plugin.py`, `conformance/__init__.py`, `conformance/test_smoke.py` (temporary; removed in Task 4)
- Modify: `mcpqa/pyproject.toml`, `requirements.txt`, `tests/test_packages.py` (no `mcp` import in `mcpqa`), `docs/requirements.csv`
- Create: `tests/test_mcpqa_core.py`

**Interfaces:**
- `target.py`:
  - `Target(kind: Literal["stdio", "http"], argv: tuple[str, ...] = (), url: str = "", token: str | None = None)`, a frozen dataclass.
  - `parse_target(text: str, *, token_env: str | None = None, env: Mapping[str, str] = os.environ) -> Target` raises `ValueError` with a usage hint. It also raises if `token_env` names a variable that isn't set.
- `wire.py`:
  - `Reply(status: int | None, message: dict | None, headers: Mapping[str, str])`, where `status` is `None` on stdio.
  - `StdioWire(argv, env=None, timeout=10.0)`:
    - `send(message: dict | bytes) -> None`;
    - `read() -> Reply | None` (`None` after the timeout);
    - `exchange(message: dict) -> Reply`, which reads until the reply whose `id` matches, or one with `id: null` for a malformed send; it raises `TimeoutError`.
    - `noise: list[str]` holds stdout lines that are not JSON.
    - `close()` kills the process. It is a context manager.
  - `HttpWire(url, token=None, timeout=10.0)`:
    - `exchange(message: dict | bytes, headers: Mapping[str, str] = {}) -> Reply` POSTs with `content-type: application/json` and `accept: application/json, text/event-stream`, and the bearer token if set.
    - It parses a `text/event-stream` body: the first `data:` JSON whose `id` matches, else the first one. A 202 with no body gives `message=None`.
    - `session_headers: dict` is added to every request (legacy session id).
  - `open_wire(target: Target, timeout: float) -> StdioWire | HttpWire`.
- `protocol.py`:
  - `REVISIONS = ("2025-11-25", "2026-07-28")`.
  - `envelope(version: str = "2026-07-28") -> dict`.
  - `modern_headers(method: str, name: str | None, version: str = "2026-07-28") -> dict`.
  - `Session(wire, revision: str)` with:
    - `open() -> dict`: for 2026, the `server/discover` result; for legacy, the `initialize` result, after which it sends `notifications/initialized` and keeps the HTTP `mcp-session-id`.
    - `request(method: str, params: dict | None = None) -> Reply`, which adds the envelope and headers for 2026; `Mcp-Name` comes from `params["name"]` or `params["uri"]`.
    - `next_id()`.
- `plugin.py`:
  - Options: `--target` (required for the `mcpqa.conformance` tests), `--token-env`, `--protocol-version {2025-11-25,2026-07-28}` (repeatable; default both), `--allow-writes`, `--mcpqa-timeout` (default 10).
  - Fixtures:
    - `mcpqa_target` (session).
    - `revision`: parametrized from `--protocol-version` and narrowed by the `@pytest.mark.mcpqa_revisions(...)` mark.
    - `session`: session-scoped per revision; it opens one wire, and skips "not applicable: the target does not serve <revision>" when `open()` fails with `-32022` or a refused handshake.
    - `fresh_wire`: function-scoped, for handshake checks.
  - `not_applicable(reason: str) -> NoReturn` skips with the message `"not applicable: " + reason`.
  - The marks `mcpqa_revisions` and `mcpqa_transport` are registered with `config.addinivalue_line`.
- Requirement `PROTO-01` (high): "`mcpqa` is a pytest plugin that takes `--target` (stdio JSON array or command, or an HTTP URL), a bearer token from the variable `--token-env` names, and `--protocol-version`; it speaks the protocol with its own client and imports neither `mcp` nor `energy_mcp`; a check whose precondition is missing skips as not applicable, with the reason."

- [ ] **Step 1: Write `tests/test_mcpqa_core.py` (PROTO-01), failing.**
  - `test_parse_target_forms`:
    - the JSON array with backslashes and spaces;
    - a shell command;
    - an HTTP URL with `--token-env` set;
    - errors for `""`, `"ftp://x"`, `"stdio:"`, `stdio:[1]`, and an unset `--token-env` variable.
  - `test_stdio_wire_exchanges_and_records_noise`: against a tiny Python echo script in `tmp_path` that prints one non-JSON line, then echoes each JSON line with `"result": {}`.
  - `test_wire_closes_its_process_on_error`: kill the child during `exchange`; it raises `TimeoutError` or `ConnectionError` within the bound, and `close()` leaves no live process.
  - `test_sse_reply_is_parsed`, with the ASGI app from `httpx2.MockTransport` or `ASGITransport`. Cases:
    - a single event;
    - two events where the second matches the id;
    - an event split across chunks;
    - a 202.
  - `test_session_opens_both_revisions` against `python -m energy_mcp`, over stdio and over in-process HTTP via `build_http_app`.
    - The `HttpWire` takes an injected `httpx2` transport for tests: `HttpWire(url, token, timeout, transport=None)`.
  - `test_plugin_reports_not_applicable`: `pytester` runs a one-check module that calls `not_applicable("no prompts")`. The outcome is skipped, and the reason line reads `not applicable: no prompts`.
  - `test_mcpqa_imports_no_mcp_sdk` (in `tests/test_packages.py`, PROTO-01): an AST scan like ENV-02's, for `mcp` and `mcp_types`.
- [ ] **Step 2: Run.** Expected: `ModuleNotFoundError: mcpqa.target`.
- [ ] **Step 3: Implement, add the pins and the entry point, and reinstall.**
- [ ] **Step 4: Run the whole suite.** Expected: all pass.
- [ ] **Step 5: Commit** `M3a: mcpqa core: target, raw wires, protocol sessions, pytest plugin (PROTO-01)`.

### Task 4: Handshake, discovery and version checks, with their broken copies (PROTO-02, PROTO-03)

**Files:**
- Create: `mcpqa/src/mcpqa/conformance/test_handshake.py`, `energy_mcp/src/energy_mcp/variants.py`, `tests/test_conformance.py`
- Modify: `energy_mcp/src/energy_mcp/__main__.py` (`--variant`), `tests/servers.py` (`run_mcpqa`), `docs/requirements.csv`; delete `conformance/test_smoke.py`

**Interfaces:**
- `variants.py`:
  - `Variant(description: str, middleware: tuple = (), outgoing: Callable[[dict], dict | None] | None = None, answer_malformed: bool = True, reject_list_cursors: bool = True, strict_unknown_tool: bool = True, startup_stdout: str | None = None, asgi: Callable[[ASGIApp], ASGIApp] | None = None)`, a frozen dataclass.
  - `VARIANTS: dict[str, Variant]`, with `EXPECTED_FAILURES: dict[str, frozenset[str]]` next to it (variant → the check names it must fail).
  - `--variant NAME` (choices = `VARIANTS`) prints `energy_mcp: running broken copy NAME` to stderr, never stdout.
- Checks in `test_handshake.py`:
  - `test_legacy_initialize` (2025-11-25, `fresh_wire`): `initialize` with `2025-11-25` gives a result whose `protocolVersion` is `2025-11-25`, a `serverInfo` with a string `name` and `version`, and a `capabilities` object.
  - `test_legacy_negotiates_unknown_version` (2025-11-25, `fresh_wire`, PROTO-D2): `initialize` with `1900-01-01` gives a result whose `protocolVersion` is in `REVISIONS`.
  - `test_discover` (2026-07-28): the result has `resultType: "complete"`, `supportedVersions` containing `2026-07-28`, and a `capabilities` object.
  - `test_unsupported_version_is_32022` (2026-07-28, PROTO-D1).
  - `test_missing_envelope_is_32602` (2026-07-28): `tools/list` with `params: {}` gets `-32602`, and HTTP 400.
  - `test_header_mismatch_is_32020` (2026-07-28, HTTP only; on stdio it is not applicable): body `tools/list` with header `Mcp-Method: prompts/list` gets 400 `-32020`.
- Broken copies:

  | Variant | Fails | How |
  |---|---|---|
  | `legacy-initialize-without-server-info` | `test_legacy_initialize` | `outgoing` drops `serverInfo` from the initialize result |
  | `echoes-unknown-version` | `test_legacy_negotiates_unknown_version` | middleware on `initialize` sets the result's `protocolVersion` to the requested one |
  | `discover-without-versions` | `test_discover` | `outgoing` drops `supportedVersions` |
  | `wrong-unsupported-version-error` | `test_unsupported_version_is_32022` | `outgoing` rewrites `-32022` to `-32602` |
  | `accepts-missing-envelope` | `test_missing_envelope_is_32602` | `outgoing` replaces that `-32602` error with `result: {}` |
  | `ignores-header-mismatch` (HTTP) | `test_header_mismatch_is_32020` | an ASGI wrapper overwrites `Mcp-Method` with the body's method before the edge |

- `tests/servers.py`:
  - `run_mcpqa(pytester, target_argv_or_url, *extra, token=None) -> pytest.RunResult` runs `pytester.runpytest_subprocess("--pyargs", "mcpqa.conformance", "--target", ..., "--mcpqa-timeout", "5", "-p", "no:cacheprovider", *extra)`.
  - `failed_checks(result) -> set[str]` and `skipped_reasons(result) -> dict[str, str]`, parsed from `-rA` output.
- Requirements:
  - `PROTO-02` (high): "On 2025-11-25, initialize returns the requested version, serverInfo and capabilities, and an unknown requested version gets another version the server serves; on 2026-07-28, server/discover lists 2026-07-28 and the capabilities, and an unsupported version is -32022 with supported and requested (HTTP 400)."
  - `PROTO-03` (high): "On 2026-07-28, a request without the _meta envelope is -32602 and, over HTTP, a header that does not match the body is -32020; both HTTP 400."
  - `PROTO-08` (high): "mcpqa's conformance checks all pass on energy_mcp over stdio and HTTP on both revisions, except checks skipped as not applicable with a listed reason; each broken copy fails exactly its checks."

- [ ] **Step 1: Write `tests/test_conformance.py` (PROTO-08, plus each check family's ID), failing.**
  - `test_clean_server_passes_over_stdio`: no failed checks. The skipped set equals `{"test_header_mismatch_is_32020": "not applicable: headers exist only over HTTP"}`.
  - `test_each_broken_copy_fails_exactly_its_checks`, parametrized over the `VARIANTS` this task adds:
    - stdio target `json.dumps([sys.executable, "-m", "energy_mcp", "--variant", name])`;
    - `failed_checks(result) == EXPECTED_FAILURES[name]`.
    - `ignores-header-mismatch` is defined here, but its test lands in Task 7 with the HTTP harness.
- [ ] **Step 2: Run.** Expected: the clean run fails with "no tests ran" (`test_handshake.py` doesn't exist), and every variant case fails.
- [ ] **Step 3: Implement the checks, `variants.py` and `--variant`. Remove the smoke check.**
- [ ] **Step 4: Run the whole suite.** Expected: all pass.
- [ ] **Step 5: Mutation proof.** Change one check's assertion (`-32022` → `-32602`): the clean run fails and that variant's set no longer matches. Revert.
- [ ] **Step 6: Commit** `M3a: handshake, discovery and version checks with broken copies (PROTO-02, PROTO-03, PROTO-08)`.

### Task 5: Capability and JSON-RPC error checks, with their broken copies (PROTO-04, PROTO-05)

**Files:**
- Create: `mcpqa/src/mcpqa/conformance/test_errors.py`, `mcpqa/src/mcpqa/conformance/test_capabilities.py`
- Modify: `energy_mcp/src/energy_mcp/variants.py`, `energy_mcp/src/energy_mcp/guard.py` (flag `strict_unknown_tool: bool = True`), `tests/test_conformance.py`, `docs/requirements.csv`

**Interfaces:**
- Checks, each on both revisions:
  - `test_parse_error_is_32700`: send the raw bytes `{"jsonrpc":"2.0","id":1,`. The reply has `error.code == -32700` and `id is None`. Over HTTP the status is 400.
  - `test_invalid_request_is_32600`: `{"jsonrpc": "2.0", "id": <n>}` gets `-32600`, with `id in (<n>, None)`. Over HTTP the status is 400.
  - `test_unknown_method_is_32601`: `mcpqa/no-such-method` gets `-32601`. Over HTTP on 2026-07-28 the status is 404.
  - `test_unknown_tool_is_32602`: `tools/call` with name `mcpqa-no-such-tool` gets `-32602`. If `tools` is not declared, it is not applicable.
  - `test_declared_capabilities_work`: for each of `tools`, `resources` and `prompts`, a declared capability's `*/list` gets a result, and an undeclared one's gets `-32601`. Capabilities come from `session.open()`.
- Broken copies:

  | Variant | Fails | How |
  |---|---|---|
  | `drops-malformed` | parse error and invalid request | `answer_malformed=False` |
  | `internal-for-unknown-method` | `test_unknown_method_is_32601` | middleware raises `-32603` for methods the server doesn't implement |
  | `unknown-tool-is-result` | `test_unknown_tool_is_32602` | `strict_unknown_tool=False`, so the SDK's `isError` result |
  | `undeclared-tools` | `test_declared_capabilities_work` | `outgoing` drops `tools` from the initialize and discover capabilities |

  A method the server doesn't implement is any method outside the server's set: the lifecycle methods, the list and get methods, `ping`, `server/discover` and `notifications/*`.
- Requirements:
  - `PROTO-04` (medium): "Every declared capability's list method answers and an undeclared one is -32601, on both revisions."
  - `PROTO-05` (high): "Invalid JSON is -32700 with id null; a non-request is -32600; an unknown method is -32601 (HTTP 404 on 2026-07-28); an unknown tool is -32602; on both revisions and transports."

- [ ] **Step 1: Add the new variants to the parametrized broken-copy test, and the new checks' names to the clean run's expectations.** Run; expected: they fail.
- [ ] **Step 2: Implement.** Run the whole suite; expected: all pass.
- [ ] **Step 3: Commit** `M3a: capability and JSON-RPC error checks with broken copies (PROTO-04, PROTO-05)`.

### Task 6: Pagination, payload and stdio-hygiene checks, with their broken copies (PROTO-06, PROTO-07)

**Files:**
- Create: `mcpqa/src/mcpqa/conformance/test_pagination.py`, `mcpqa/src/mcpqa/conformance/test_payloads.py`
- Modify: `variants.py`, `tests/test_conformance.py`, `docs/requirements.csv`

**Interfaces:**
- Checks:
  - `test_pagination_is_complete` (both revisions):
    - For each declared list method, follow `nextCursor` (at most 1000 pages) until it is absent.
    - Every item key is unique: `name` for tools and prompts, `uri` for resources, `uriTemplate` for templates.
    - More than 1000 pages fails as "pagination did not end".
  - `test_invalid_cursor_is_32602` (both revisions): each declared list method with `cursor="mcpqa-invalid-cursor"` gets `-32602`.
  - `test_large_argument_is_handled` (both revisions):
    - Pick the first read-only tool whose `inputSchema` has a `string` property. If there is none, it is not applicable.
    - Call it with that property set to 1 MiB of `"x"`.
    - The reply is a result (`isError` either way) or a `-32602` error, never `-32603` or silence.
    - The next `tools/list` is answered.
  - `test_stdout_carries_only_messages` (stdio only; over HTTP it is not applicable): after the session's other exchanges, `wire.noise == []`.
- Broken copies:

  | Variant | Fails | How |
  |---|---|---|
  | `accepts-any-cursor` | `test_invalid_cursor_is_32602` | `reject_list_cursors=False` |
  | `endless-pages` | `test_pagination_is_complete` | middleware on `tools/list` returns the same page with `nextCursor: "again"` for no cursor or `"again"`; other cursors still go to the guard |
  | `crashes-on-large-argument` | `test_large_argument_is_handled` | middleware raises `-32603` when any argument string is over 100 000 characters |
  | `prints-to-stdout` | `test_stdout_carries_only_messages` | `startup_stdout="energy_mcp starting"`, written to real stdout before serving |

- Requirements:
  - `PROTO-06` (medium): "Every declared list method paginates to an end without repeating an item, and an invalid cursor is -32602."
  - `PROTO-07` (medium): "A 1 MiB string argument gets a clean answer (a result or -32602, never -32603 or silence) and the server keeps answering; over stdio, stdout carries only MCP messages."

- [ ] **Step 1: Add the new variants and expectations to the tests.** Run; expected: they fail.
- [ ] **Step 2: Implement.** Run the whole suite; expected: all pass.
- [ ] **Step 3: Commit** `M3a: pagination, payload and stdout checks with broken copies (PROTO-06, PROTO-07)`.

### Task 7: The clean server over HTTP, the CLI, docs (PROTO-08)

**Files:**
- Modify: `tests/test_conformance.py`, `tests/servers.py`, `README.md` (an "mcpqa" section with the commands), `docs/decisions.md` (Held by)

**Interfaces:**
- `test_clean_server_passes_over_http`:
  - A real `energy_mcp --transport http` process (the `authenticated_http_server` pattern from `test_transports.py`, moved into `tests/servers.py`).
  - The token is minted on the wall clock with the read scopes, and passed via `--token-env MCPQA_TOKEN` (`monkeypatch.setenv`).
  - Expected: no failed checks. The skipped set equals `{"test_stdout_carries_only_messages": "not applicable: stdout exists only over stdio"}`.
- `ignores-header-mismatch` moves onto that HTTP harness: a real process started with `--variant`.

- [ ] **Step 1: Write the test.** Run; expected: it fails until the harness exists.
- [ ] **Step 2: Implement.** Run the whole suite and time it; record the run time in the ledger. If the conformance tests take more than 3 minutes on Windows, rule on sharing one server process per revision.
- [ ] **Step 3: Update the README and fill in decisions' Held by. Commit** `M3a: clean HTTP conformance run, README, decisions held-by`.

### Task 8: Lesson 03 (after the final review's fix pass)

- [ ] Write `C:\Users\yspizhoviy\mcp-server-qa-lessons\03-conformance.md`. It covers:
  - JSON-RPC error codes from zero;
  - what "conformance" means and why it is tested from outside;
  - version negotiation on both revisions;
  - why the checker must not share the server's SDK;
  - broken copies as tests of the tests.

  It includes `file:line` references at the branch head, an exercise (run `mcpqa` on `--variant drops-malformed` in PowerShell, with the exact expected output from a real run), and seven interview questions with model answers.
