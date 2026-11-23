"""Pagination: every list ends, never repeats an item, and refuses a cursor it didn't issue.

Spec: server utilities, "Pagination" (both revisions): the cursor is opaque, a missing
`nextCursor` ends the results, and invalid cursors SHOULD get -32602.
"""

from __future__ import annotations

from mcpqa.expect import error_of, result_of
from mcpqa.plugin import not_applicable
from mcpqa.protocol import Session

MAX_PAGES = 1000
# capability -> [(list method, result key, item key)]
LISTS = {
    "tools": [("tools/list", "tools", "name")],
    "resources": [("resources/list", "resources", "uri"),
                  ("resources/templates/list", "resourceTemplates", "uriTemplate")],
    "prompts": [("prompts/list", "prompts", "name")],
}


def declared_lists(session: Session) -> list[tuple[str, str, str]]:
    lists = [entry for capability, entries in LISTS.items() if capability in session.capabilities for entry in entries]
    if not lists:
        not_applicable("the target declares no tools, resources or prompts")
    return lists


def test_pagination_is_complete(revision: str, session: Session) -> None:
    for method, result_key, item_key in declared_lists(session):
        seen: set[str] = set()
        cursor = None
        for _ in range(MAX_PAGES):
            result = result_of(session.request(method, None if cursor is None else {"cursor": cursor}))
            items = result.get(result_key)
            assert isinstance(items, list), f"{method}: no {result_key} list"
            for item in items:
                key = item.get(item_key) if isinstance(item, dict) else None
                assert key not in seen, f"{method}: {key!r} came back on a later page"
                seen.add(key)
            cursor = result.get("nextCursor")
            if cursor is None:
                break
        else:
            raise AssertionError(f"{method}: pagination did not end after {MAX_PAGES} pages")


def test_invalid_cursor_is_32602(revision: str, session: Session) -> None:
    for method, _, _ in declared_lists(session):
        error_of(session.request(method, {"cursor": "mcpqa-invalid-cursor"}), -32602)
