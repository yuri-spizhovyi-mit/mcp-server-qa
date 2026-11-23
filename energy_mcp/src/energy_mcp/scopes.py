"""The scope policy (AUTH-D4): which scope each call needs, and which scope implies which.

Pure functions, shared by the HTTP edge (403) and by `ScopeGuard` (every transport). They
accept whatever a caller sent (any JSON value as `params`) and never raise. A scope string
they return comes only from the fixed vocabulary or from an alert ID that matched its ASCII
pattern, so it is safe to put in a `WWW-Authenticate` header.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

BASE_SCOPES = ("meters:read", "alerts:read", "alerts:write", "reports:read")
READ_SCOPES = frozenset({"meters:read", "alerts:read", "reports:read"})
WRITE_ANY_ALERT = "alerts:write"
ALERT_ID = re.compile(r"ALR-[0-9]{4}")  # fullmatch; same rule as the tool's inputSchema

TOOL_SCOPES = {"list_meters": "meters:read", "get_readings": "meters:read", "list_alerts": "alerts:read"}
METHOD_SCOPES = {"resources/read": "reports:read", "prompts/get": "alerts:read"}


def _write_scope(alert_id: Any) -> str:
    if isinstance(alert_id, str) and ALERT_ID.fullmatch(alert_id):
        return f"{WRITE_ANY_ALERT}:{alert_id}"
    return WRITE_ANY_ALERT  # missing or malformed: the broad scope, and no caller text


def is_known(scope: str) -> bool:
    if scope in BASE_SCOPES:
        return True
    prefix, _, alert_id = scope.rpartition(":")
    return prefix == WRITE_ANY_ALERT and ALERT_ID.fullmatch(alert_id) is not None


def needed(method: Any, params: Any) -> frozenset[str]:
    """The scopes a call needs; empty for listing, unknown tools and every other method."""
    if method in METHOD_SCOPES:
        return frozenset({METHOD_SCOPES[method]})
    if method != "tools/call" or not isinstance(params, dict):
        return frozenset()
    name = params.get("name")
    if name == "acknowledge_alert":
        arguments = params.get("arguments")
        return frozenset({_write_scope(arguments.get("alert_id") if isinstance(arguments, dict) else None)})
    return frozenset({TOOL_SCOPES[name]}) if isinstance(name, str) and name in TOOL_SCOPES else frozenset()


def allows(granted: Iterable[str], scope: str) -> bool:
    """True when `granted` holds `scope` or a broader scope implying it (alerts:write covers alerts:write:ALR-NNNN)."""
    granted = set(granted)
    if scope in granted:
        return True
    return scope.startswith(f"{WRITE_ANY_ALERT}:") and WRITE_ANY_ALERT in granted


def missing(granted: Iterable[str], needs: Iterable[str]) -> list[str]:
    granted = list(granted)
    return sorted(s for s in needs if not allows(granted, s))
