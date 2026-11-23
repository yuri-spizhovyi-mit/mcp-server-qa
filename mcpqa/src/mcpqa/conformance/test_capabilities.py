"""Declared capabilities match what the server serves.

Spec: lifecycle "Capability Negotiation" (2025-11-25: "only use capabilities that were
successfully negotiated"); schema MethodNotFoundError (2026-07-28: a method gated behind an
unadvertised capability is -32601).
"""

from __future__ import annotations

from mcpqa.expect import error_of, result_of
from mcpqa.protocol import Session

LISTS = {"tools": "tools/list", "resources": "resources/list", "prompts": "prompts/list"}


def test_declared_capabilities_work(revision: str, session: Session) -> None:
    for capability, method in LISTS.items():
        reply = session.request(method)
        if capability in session.capabilities:
            result_of(reply)
        else:
            error_of(reply, -32601)
