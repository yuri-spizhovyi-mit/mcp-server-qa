# CLAUDE.md

## What this is

`mcp-server-qa` is **QA for the tool layer of AI agents**: a small MCP server
(`energy_mcp`, synthetic energy data) and the test suite that proves it follows the
protocol, keeps its tool contracts, resists attacks, and works for an agent that uses it.
**The test suite is the product.** Part of it (`mcpqa`) is reusable against any MCP server.

Portfolio and learning project. Roadmap, milestones and definition of done:
[`docs/plan.md`](docs/plan.md). Until that plan is approved, no code is written.

## Hard constraints

- **No money, ever.** No API keys, no paid API usage, no "buy credits". CI never has a secret.
- **Every test and CI run is deterministic and free.** Seed randomness; use a fake clock for
  time-dependent behaviour (rate limits, token expiry, timeouts where possible).
- **Models:** the scripted provider in CI; Ollama only on the developer's laptop, never in CI;
  recorded Ollama runs (cassettes) are replayed in CI. A manual Claude demo (Claude Code or the
  desktop app, no API key) is allowed for the README.
- **Data is synthetic.** No real people, companies, places or addresses.
- **Don't guess the SDK.** The MCP Python SDK is v2 (`MCPServer`, `Client`, `mcp-types`).
  Check its docs (https://py.sdk.modelcontextprotocol.io/) and the spec
  (https://modelcontextprotocol.io/specification/) before using an API or citing a rule.

## Working rules

- One branch per milestone, `feature/<slug>` (slugs in `docs/plan.md`); a PR into `main`;
  merge only with green CI **and the owner's OK**. Never skip, disable or loosen a test
  without a written reason in the PR and in `docs/decisions.md`.
- Run the full suite before pushing. Do not push red.
- **Ask the owner before:** creating the GitHub repo (public, under `yuri-spizhovyi-mit`),
  merging, tagging, or releasing.
- When giving a command to run, say where it runs (Windows PowerShell, WSL Ubuntu, or CI).
- **Safety lives in code, and tests prove it.** A guardrail is never only a sentence in a tool
  description: the server enforces it, deterministic tests prove it, and agent runs measure how
  often a model *tries* to break it.

## Test conventions

- Every test has `@pytest.mark.req("ID")` (prefixes in `docs/plan.md`); module-level
  `pytestmark` is fine. A new requirement goes into `docs/requirements.csv` first; then run
  `python scripts/rtm.py` and commit `docs/rtm.*`. CI fails on an untested requirement, an
  uncatalogued ID or a stale matrix.
- **A test that can't fail proves nothing.** Every reusable check in `mcpqa` has a broken copy
  of the server it must catch.
- **Score the world, not the words:** an agent task checks server state and the tool calls
  made before it checks anything the agent says.
- **Non-determinism is measured, not hoped away:** an agent result is a rate over repeated runs
  with a confidence interval, never a single pass/fail.
- Findings (planted, real, or bugs in the harness itself) go in `docs/findings.md` with
  evidence. Open behaviour choices go in `docs/decisions.md`, each with a test.

## PRs

Every PR description says what the tests found and explains one test as a teaching example.

## Lessons

Each milestone also has a lesson in `C:\Users\yspizhoviy\mcp-server-qa-lessons\` (outside the
repo), `NN-<slug>.md`: the concept from zero, how this repo does it (`file:line`), a hands-on
exercise with the expected result, and interview questions with model answers.
