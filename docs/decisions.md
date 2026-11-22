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
