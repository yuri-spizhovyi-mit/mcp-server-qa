# mcp-server-qa: Project Plan

**QA for the tool layer of AI agents.** We build a small MCP server and a test suite that
proves it follows the protocol, keeps its tool contracts, resists attacks, and works for an
agent that uses it. **The test suite is the product.** Part of it is reusable: it can be
pointed at any MCP server.

Status: **approved 2026-11-22.** Milestone 0 is done; milestone 1 is in progress.

## The idea in plain words

An AI agent is a language model that can act: it calls *tools* (search, read a file, send a
message). MCP (the Model Context Protocol) is the standard plug between agents and tools: a
server publishes tools, resources and prompts; any MCP client (Claude Code, the Claude
desktop app, an IDE, an agent framework) can discover and call them.

That plug is a new attack surface and a new source of bugs:
- a tool's *description* is text the model reads, so it can hide instructions ("tool
  poisoning"), or change after the user approved it ("rug pull");
- a tool's *output* is text the model reads, so data can carry a prompt injection;
- a broken schema or a wrong error code breaks every agent that uses the server;
- a vague description makes agents pick the wrong tool, which no unit test notices.

This project tests all of that, deterministically and for free.

## What you'll be able to say in an interview

> "I built an MCP server and a test suite for it. Part of the suite is a reusable tool:
> you point it at any MCP server and it checks protocol conformance on two protocol
> versions, fuzzes every tool against its input schema, validates outputs against the
> declared output schema, and scans tool descriptions for poisoning and for changes after
> approval. I proved the suite works by running it against deliberately broken copies of
> the server: every planted bug has a test that catches it. The server is an OAuth 2.1
> resource server with per-tool scopes, and I attacked it with forged and tampered tokens,
> path traversal and scope escalation. I measured how the *wording* of tool descriptions
> changes an agent's task success rate, with confidence intervals, and a release gate
> blocks a change that breaks a contract, adds an unsafe description, or is unmeasured.
> All of it runs free: no API keys, a scripted model in CI and a local model on my laptop."

## Hard constraints

- **No money.** No API keys, no paid API usage, ever. CI never needs a secret.
- **Every test and every CI run is deterministic and free.** Randomness is seeded; time is a
  fake clock where it matters.
- **Models:** a scripted fake model in CI; a free local model through Ollama on your laptop
  (never in CI), run as overnight batches that M6 and M7 depend on; recordings of the local model replayed in CI. A manual demo with
  your Claude plan (Claude Code or the desktop app connected to the server) is allowed for
  the README; it needs no API key.
- **Data is synthetic.** No real people, companies, places or addresses.
- **Every milestone is reachable offline.** No "done when" depends on credits, a paid service
  or a model you don't have. (llm-agent-qa's M8 and M9 closed partly unmet because they
  waited on API credits; this plan avoids that by design.)

## What we build on (checked 2026-11-22)

| Thing | Version | Note |
|---|---|---|
| MCP specification | **2026-07-28** (latest) and **2025-11-25** | 2026-07-28 has **no initialize handshake and no session**: each request carries its protocol version and capabilities in `_meta`; `server/discover` is new; server-to-client requests, `ping` and `logging/setLevel` are gone; new error codes `-32020` HeaderMismatch, `-32021` MissingRequiredClientCapability, `-32022` UnsupportedProtocolVersion. 2025-11-25 still uses `initialize`. |
| MCP Python SDK (`mcp`) | 2.3.0 | `MCPServer` (was `FastMCP`), `Client`, wire types in `mcp-types`. Serves both revisions above. Its argument checking is **lax** (pydantic coerces `"2"` and `true` to integers and drops unknown arguments: F-02), an unknown tool comes back as an `isError` result instead of `-32602` (F-01), and an unexpected exception in a tool becomes an `isError` result with a generic text, not a `-32603` error (F-04 corrects what this row first said). The server adds its own strict checks. |
| SDK auth | 2.3.0 | The server is an OAuth 2.1 resource server: we supply a `TokenVerifier` and `AuthSettings`; the SDK serves the RFC 9728 metadata and the 401. `required_scopes` is server-wide, so **per-tool scopes and the 403 `insufficient_scope` are ours to build.** Auth applies to HTTP only; stdio takes credentials from the environment. |
| MCP TypeScript SDK | 1.32.2 | one interop check only (M3) |
| Python | 3.12 | |
| pytest, Hypothesis, hypothesis-jsonschema | 9.2.0, 6.170.17, 0.23.2 | exact pins are set in M0 |
| Ollama | latest, Windows native | your laptop only (32 GB RAM, i7-10850H, no GPU: a 3-8B model on CPU, about a minute per agent task). Required for M6 and M7: one overnight batch (~7 h) in M6, ~2 h for each later PR that changes the tool surface |

Spec facts not yet read in full (tool annotations, resource templates, progress) are checked
in the milestone that uses them, and quoted with a link in that milestone's lesson.

## Architecture

```text
                          ┌──────────────────── energy_mcp (the server) ────────────────────┐
 MCP client ──stdio──────►│ transport ─► auth (HTTP: JWT verifier, audience, per-tool        │
 (Claude, our agent,      │              scopes, 403 insufficient_scope) ─► rate limit ─►    │
  mcpqa, TS SDK client) ─►│ tools · resources (reports://) · prompts ─► synthetic energy data│
            Streamable    └──────────────────────────────────────────────────────────────────┘
            HTTP + OAuth            ▲ variants: clean | broken copies (one planted bug each)
                                    │           | tool-surface variants (clear/vague/loose/poisoned)
 test issuer (tests only) ──JWTs────┘

 mcpqa (the reusable suite) ──--target "stdio:<cmd>" | "http://…/mcp"──► any MCP server
   pytest plugin: conformance (both revisions) · contracts (fuzz inputs, validate outputs)
   CLI: mcpqa scan (poisoning + rug-pull pins) · mcpqa diff (surface vs last release)

 agent (tests/agent) ──► MCP client + tool-use loop ──► energy_mcp
   providers: scripted (CI) | ollama (laptop) | replay (recorded ollama runs, CI)
```

## The two packages

- **`energy_mcp`**: the server. A seeded generator builds the same world every run (about 20
  meters at invented sites, interval readings in kWh, alerts with free-text technician
  notes). Tools: `list_meters`, `get_readings` (paginated), `list_alerts`,
  `acknowledge_alert` (the only write). Resource: `reports://{name}` over a folder of
  synthetic report files. Prompt: `investigate_alert(alert_id)`. Every tool declares an
  `outputSchema` and returns `structuredContent` plus a text copy. Also contains the
  **broken copies** and the **tool-surface variants**, selected by a command-line flag, so
  every test can start exactly the server it needs.
- **`mcpqa`**: the reusable suite. A pytest plugin (adds `--target`, `--protocol-version`)
  and a CLI. It uses only what any server exposes publicly. Installable on its own
  (`pip install ./mcpqa`), and the repo's own `tests/` use it the way an outsider would.

Tests that need inside knowledge (energy logic, scopes, tokens, rate limits, agent tasks)
live in `tests/` and are specific to `energy_mcp`.

Rules for `mcpqa` on a server we didn't write (my calls):
- **Read-only by default.** It calls only tools annotated `readOnlyHint: true`; any other
  tool is called only with `--allow-writes`. Fuzzing someone else's write tools is opt-in.
- **Capability-aware.** A check that needs something the target doesn't declare (no
  resources, no slow tool for cancellation, no pagination) reports "not applicable" with the
  reason. It never passes silently.
- **An optional target profile** (a small YAML file) can name a slow tool, a long list or
  known-valid IDs. Without one, the checks that need them report "not applicable".
- A schema-valid input that the server rejects as a tool error (`isError: true`, for example
  an ID that doesn't exist) counts as clean handling. A `-32603` crash or an output that
  breaks `outputSchema` is a failure.

## Milestones

Each milestone is one branch `feature/<slug>` and one PR into `main`, merged only with green
CI and your OK. A large milestone may land as numbered parts, each its own PR. Lesson *N*
belongs to milestone *N* and is written in the lesson folder (below), not in the repo.

| # | Slug | Scope | Done when | Lesson | Status |
|---|---|---|---|---|---|
| 0 | scaffold | this plan, `CLAUDE.md`, repo layout with both packages as empty installable packages, exact pins, `pytest.ini` with the `req` marker, `docs/requirements.csv`, `scripts/rtm.py`, `findings.md`, `decisions.md`, CI on Ubuntu **and** Windows | CI green on both OSes; CI fails on a test without `req`, on an uncatalogued ID and on a stale matrix (each shown by a test); `pip install -e` gives both packages | **00**: what MCP is, why agents need it, how this project tests it; plus requirements traceability | Done (PR #1) |
| 1 | server | `energy_mcp` without auth: seeded domain, the four tools, `reports://` resource, prompt, strict argument validation (the SDK's is lax: F-02), stdio and Streamable HTTP, both protocol revisions; deterministic tests through the SDK's in-memory `Client` | every tool's output on the seeded world matches its `outputSchema`; bad arguments give the documented error on both transports; a manual run connects Claude Code or the desktop app over stdio and calls a tool (screenshot in `docs/evidence/`) | **01**: JSON-RPC 2.0 from zero; tools, resources, prompts; transports; what changed between 2025-11-25 and 2026-07-28 | In progress |
| 2 | auth | test issuer (JWTs signed with a key made at test time); `TokenVerifier` checking signature, expiry, issuer, audience; per-tool scopes; per-alert write scopes (`alerts:write:ALR-0042`), with plain `alerts:write` covering every alert (the spec requires servers to handle scope hierarchies); 403 `insufficient_scope` with `WWW-Authenticate`; RFC 9728 metadata; per-token rate limit on a fake clock; stdio identity from environment | missing / expired / wrong-audience / wrong-issuer / `alg: none` / re-signed tokens are rejected with the spec's status and header; each tool refuses a token without its scope; a token for one alert can't acknowledge another; call N+1 in a window is limited, and the window resets on the fake clock | **02**: OAuth 2.1 from zero: resource server vs authorization server, JWT, audience, scopes, 401 vs 403; rate limiting | |
| 3 | conformance | `mcpqa` pytest plugin with `--target`; on both revisions: discovery / `initialize`, capability declaration, version negotiation and `-32022`, JSON-RPC errors (parse, invalid request, method not found, invalid params), cancellation (stdio notification; HTTP stream close), client timeouts, large payloads and pagination, both transports; one TypeScript SDK client interop check; first **broken copies** | the plugin passes on `energy_mcp` and fails, with the right test, on each conformance broken copy (wrong error code, ignores cancellation, wrong version answer, ...); it also runs in CI against a server we didn't write (the official `@modelcontextprotocol/server-everything`, pinned), where each check passes, fails with a recorded finding, or reports "not applicable" with its reason; the TS client lists and calls a tool | **03**: protocol conformance testing; version negotiation; JSON-RPC error codes; cancellation and timeouts | |
| 4 | contracts | Hypothesis fuzzing of every tool from its own `inputSchema` (valid inputs must succeed and match `outputSchema`; invalid inputs must fail cleanly, never `-32603`); output validation; a **surface snapshot** (tools, schemas, description hashes, resources, prompts) and `mcpqa diff` with written compatibility rules (breaking / compatible / needs review); a `v2` surface of `energy_mcp` to compare | fuzzing finds every contract broken copy (output breaks schema, crash on an edge value, accepts what its schema forbids); `mcpqa diff` classifies a set of planted changes exactly as the rules say | **04**: JSON Schema from zero; property-based testing and shrinking; API contracts and backward compatibility | |
| 5 | security | `mcpqa scan`: poisoning heuristics on descriptions and schemas (hidden instructions, invisible Unicode, cross-tool references), **rug-pull pins** (an approval file of description hashes; any change after approval is flagged); prompt injection carried in tool outputs (alert notes); path traversal on `reports://` (`../`, encoded, absolute, Windows and POSIX forms); scope escalation; token replay across audiences; rate-limit abuse; a scripted worst-case agent that obeys every injection | every security broken copy is flagged; the worst-case agent obeys every injection and still cannot acknowledge any alert except the one its per-alert token names, write with a read-only token, or read outside `reports/`; the limit is written down as a finding: with a broad `alerts:write` token the injected write goes through, and M6 measures how often a real model tries it | **05**: the MCP threat model: tool poisoning, rug pulls, indirect prompt injection, confused deputy, path traversal; why safety lives in code | |
| 6 | agent | MCP client + tool-use loop; providers: scripted, ollama, replay; about 20 tasks scored on world state and on answers computed from the seeded data; **tool-surface variants**: clear, vague descriptions, loose schemas, one poisoned; repeated runs; Wilson intervals; Fisher's exact test between variants; local Ollama batches recorded as cassettes | in CI, the scripted "always right" agent scores 100% and "always wrong" 0%, and the replayed local runs reproduce the report exactly; the report gives each variant's success rate ± interval and the measured differences, from one recorded overnight batch: 20 tasks x 5 tries x 4 variants (~7 h on the laptop). The report states the smallest drop that sample can detect (about 20 percentage points) | **06**: agents in the loop; non-determinism; success rates and confidence intervals; why tool wording is part of the contract | |
| 7 | gate | `release-gate` workflow: compares the candidate with the last release using `mcpqa diff`, `mcpqa scan`, conformance and contracts; a changed description or schema must come with a fresh recorded local run, or it is blocked as **unmeasured**; agent rates compared from cassettes | planted bad candidates (breaking schema change, poisoned description, unmeasured description change, worse measured agent rate) are each blocked with a readable reason; a neutral change passes | **07**: release gates; contract diffs; why "unmeasured" is a failure, not a pass | |
| 8 | release | README story (2-minute read), the manual Claude demo, the generated traceability matrix, evidence pack attached to the release, v1.0.0 | a reviewer gets the story in 2 minutes; the `v1.0.0` release carries its evidence and is refused if the run failed | **08**: presenting tool-layer QA work; the interview walk-through | |

Gate rules for agent rates (my calls, recorded as GATE-D decisions in M7):
- every cassette records the surface hash, the provider, the model name and version, and the
  seed. A recording is **fresh** only if its surface hash equals the candidate's;
- a candidate is **worse** when a one-sided Fisher's exact test against the last release's
  recording gives p < 0.05. A drop in the point estimate alone doesn't block. The report
  states the smallest drop the sample size could detect, so a pass is never read as "no
  change".

Before v1.0.0 exists, "the last release" in M4 and M7 is a `v0.x` tag cut from `main` with
your OK (proposed: `v0.1.0` after M2). The gate's job is shown on planted candidates either way.

## Requirement IDs

Every test carries `@pytest.mark.req("ID")`. A requirement goes into `docs/requirements.csv`
first; `scripts/rtm.py` builds `docs/rtm.md` and `docs/rtm.csv`; CI fails on an untested
requirement, an uncatalogued ID or a stale matrix.

| Prefix | Area | Milestone |
|---|---|---|
| ENV | repo, CI, traceability, release | 0, 8 |
| SRV | server behaviour: domain, tools, resources, prompts | 1 |
| AUTH | tokens, scopes, metadata, rate limit | 2 |
| PROTO | protocol conformance, both revisions, transports | 3 |
| CONT | input fuzzing, output schemas, compatibility | 4 |
| SEC | scanner, injection, traversal, escalation | 5 |
| AGT | agent loop, providers, task success | 6 |
| GATE | release gate | 7 |

A decision gets an ID with a `-D` suffix (`AUTH-D1`) in `docs/decisions.md`; a finding gets
`F-NN` in `docs/findings.md`.

## Repository layout

```text
energy_mcp/          the server package (src layout): domain, tools, auth, variants
mcpqa/               the reusable suite package: pytest plugin, scanner, diff, CLI
tests/               this repo's tests (use the mcpqa plugin + energy_mcp internals)
tests/agent/         agent loop, providers, tasks (M6)
cassettes/           recorded local-model runs, replayed in CI (M6)
interop/ts/          the TypeScript SDK interop check (M3)
scripts/             rtm.py, evidence pack
docs/                plan, decisions, findings, requirements, rtm, evidence, reports
.github/workflows/   ci.yml (every PR, Ubuntu + Windows); release-gate.yml (M7); release.yml (M8)
```

## Lessons

Folder: **`C:\Users\yspizhoviy\mcp-server-qa-lessons\`** (outside the repo; created with
lesson 00). One Markdown file per milestone, `NN-<slug>.md`. Each lesson:

1. explains the concept in plain words, from zero;
2. shows how this repo does it, with `file:line` references (checked when the milestone
   merges, so they point at `main`);
3. has a hands-on exercise with the exact command, where it runs, and the expected output;
4. ends with interview questions and model answers.

## Where commands run

| What | Where |
|---|---|
| Python, pytest, `mcpqa`, git, `gh` | **Windows PowerShell**, in the repo folder |
| Ollama (M6, M7) | **Windows**, native installer from ollama.com; no WSL needed. Batches run overnight in PowerShell |
| Node (TypeScript interop check) | **Windows PowerShell** (Node 24 is installed) |
| CI | GitHub Actions, Ubuntu and Windows runners (free for public repos) |
| WSL Ubuntu | only to reproduce a Linux-only CI failure locally |

CI runs on both operating systems because the path-traversal tests and stdio subprocess
handling behave differently on Windows and Linux.

## Decisions already made (with you)

- Cover protocol revisions **2026-07-28 and 2025-11-25**.
- Auth is the **spec's OAuth shape with a local test issuer**; no external identity provider.
- The suite is **partly reusable** (`--target`), proven on **broken copies in CI**.
- The agent milestone **compares tool-surface variants**, not one baseline rate.
- **You run the local Ollama batches overnight**: ~7 h once in M6, ~2 h for each later PR
  that changes a tool description or schema. The gate blocks such a PR until its batch is
  recorded.
- **One repo, two Python packages** (`energy_mcp`, `mcpqa`).
- **Per-alert write scopes** stop an injected write in code (the confused-deputy case);
  broad `alerts:write` stays available, and its risk is a documented, measured limit.
- Lessons live in `C:\Users\yspizhoviy\mcp-server-qa-lessons\`.

## Calls I made (check these)

- Milestones are numbered from 0 so lesson *N* matches milestone *N*.
- Server and auth are separate milestones (1 and 2), to keep each PR reviewable.
- CI runs on Ubuntu and Windows.
- A `v0.1.0` tag after M2 is the first "last release" for compatibility checks (needs your OK
  when we get there).
- One TypeScript interop check, nothing more in TypeScript.
- Whether a missing scope fails at HTTP level (403) or as a tool error is decided in M2 and
  recorded as `AUTH-D1`; my starting position is 403, as the spec suggests.
- Rate-limit response shape (HTTP 429 vs a JSON-RPC error) is decided in M2 (`AUTH-D2`).
