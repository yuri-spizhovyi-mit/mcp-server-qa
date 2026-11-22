"""Requirements traceability matrix: requirement -> tests, read from the tests themselves.

    python scripts/rtm.py            # write docs/rtm.csv and docs/rtm.md; exit 1 on a gap
    python scripts/rtm.py --check    # verify only; write nothing

Links come from `@pytest.mark.req("ID", ...)` on each test (function, async function, or
method of a `Test*` class), or from a module- or class-level `pytestmark`; never from a
hand-kept list. The catalogue, `docs/requirements.csv`, says what each ID means. The check
fails when:
- a catalogued ID is malformed, duplicated, or has an unknown risk;
- a catalogued requirement has no test;
- a test cites an ID that isn't catalogued, or cites none;
- `docs/rtm.csv` or `docs/rtm.md` is out of date.
A parametrized test counts once. ENV-04's tests run this check inside pytest, so CI fails on
any of the above. Adapted from llm-agent-qa's scripts/rtm.py.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID_PATTERN = re.compile(r"^(ENV|SRV|AUTH|PROTO|CONT|SEC|AGT|GATE)-\d{2}$")
RISKS = ("low", "medium", "high")
TestDef = ast.FunctionDef | ast.AsyncFunctionDef


def _req_ids(nodes) -> list[str]:
    """IDs in `pytest.mark.req(...)` calls among decorator or pytestmark nodes."""
    ids = []
    for node in nodes:
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "req":
            ids += [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return ids


def _pytestmark(body) -> list[str]:
    """IDs from a `pytestmark = ...` assignment (one mark, or a list or tuple of marks) in a body."""
    ids = []
    for node in body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets):
            value = node.value
            ids += _req_ids(value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value])
    return ids


def links(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """{requirement ID: [test ids]} and the tests that cite no requirement, for root/tests."""
    found: dict[str, list[str]] = defaultdict(list)
    untraced = []

    def record(test_id: str, ids: list[str]) -> None:
        if not ids:
            untraced.append(test_id)
        for req in dict.fromkeys(ids):
            found[req].append(test_id)

    def scan(body, prefix: str, inherited: list[str]) -> None:
        for node in body:
            if isinstance(node, TestDef) and node.name.startswith("test_"):
                record(f"{prefix}::{node.name}", _req_ids(node.decorator_list) or inherited)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                marks = _req_ids(node.decorator_list) or _pytestmark(node.body) or inherited
                scan(node.body, f"{prefix}::{node.name}", marks)

    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scan(tree.body, path.relative_to(root).as_posix(), _pytestmark(tree.body))
    return found, untraced


def catalogue(path: Path) -> list[dict[str, str]]:
    """The requirement rows; `utf-8-sig` tolerates the BOM that Excel adds on Windows."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def problems(rows: list[dict[str, str]], found: dict[str, list[str]], untraced: list[str]) -> list[str]:
    known = {r["id"] for r in rows}
    out = [f"{r['id']} is not a valid requirement ID" for r in rows if not ID_PATTERN.match(r["id"])]
    out += [f"{r['id']} has risk {r['risk']!r}; use low, medium or high" for r in rows if r["risk"] not in RISKS]
    out += [f"{req} is catalogued twice" for req, n in Counter(r["id"] for r in rows).items() if n > 1]
    out += [f"{req} has no test" for req in dict.fromkeys(r["id"] for r in rows) if not found.get(req)]
    out += [f"{req} is cited by {', '.join(tests)} but not catalogued in docs/requirements.csv"
            for req, tests in sorted(found.items()) if req not in known]
    out += [f"{t} cites no requirement" for t in untraced]
    return out


def render_csv(rows: list[dict[str, str]], found: dict[str, list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "area", "risk", "requirement", "tests", "test_ids"])
    for r in rows:
        tests = found.get(r["id"], [])
        writer.writerow([r["id"], r["area"], r["risk"], r["requirement"], len(tests), "; ".join(tests)])
    return buffer.getvalue()


def render_md(rows: list[dict[str, str]], found: dict[str, list[str]]) -> str:
    total = sum(len(found.get(r["id"], [])) for r in rows)
    traced = sum(bool(found.get(r["id"])) for r in rows)
    areas: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        areas[r["area"]].append(r)
    lines = [
        "# Requirements traceability matrix", "",
        "Generated by `scripts/rtm.py` from the tests themselves (`@pytest.mark.req`); do not edit by",
        "hand. CI fails if a requirement has no test, a test cites an unknown requirement or none, or",
        "this file is stale (ENV-04). Full test lists: [`rtm.csv`](rtm.csv). The requirements:",
        "[`requirements.csv`](requirements.csv).", "",
        f"**{len(rows)} requirements, {traced} traced: {total} requirement-test links.**", "",
        "| Area | Requirements | Links | High-risk |", "|---|---|---|---|",
    ]
    for area, group in areas.items():
        area_links = sum(len(found.get(r["id"], [])) for r in group)
        high = sum(r["risk"] == "high" for r in group)
        lines.append(f"| {area} | {len(group)} | {area_links} | {high} |")
    lines += ["", "| ID | Risk | Requirement | Tests |", "|---|---|---|---|"]
    for r in rows:
        tests = found.get(r["id"], [])
        names = "<br>".join(f"`{t.split('::', 1)[1]}`" for t in tests) or "**none**"
        lines.append(f"| {r['id']} | {r['risk']} | {r['requirement']} | {names} |")
    return "\n".join(lines) + "\n"


def _outputs(root: Path) -> list[tuple[Path, str]]:
    rows = catalogue(root / "docs" / "requirements.csv")
    found, _ = links(root)
    return [(root / "docs" / "rtm.csv", render_csv(rows, found)), (root / "docs" / "rtm.md", render_md(rows, found))]


def write(root: Path = ROOT) -> None:
    """Write docs/rtm.csv and docs/rtm.md, always with LF line endings."""
    for path, text in _outputs(root):
        path.write_bytes(text.encode("utf-8"))


def check(root: Path = ROOT) -> list[str]:
    """Everything wrong with the traceability, as messages; empty when it's complete and current."""
    found, untraced = links(root)
    out = problems(catalogue(root / "docs" / "requirements.csv"), found, untraced)
    for path, text in _outputs(root):
        # read_text folds CRLF to LF, so a Windows (autocrlf) checkout is not "stale"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            out.append(f"{path.relative_to(root).as_posix()} is stale: run python scripts/rtm.py")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repo)")
    args = parser.parse_args(argv)
    if not args.check:
        write(args.root)
    issues = check(args.root)
    for issue in issues:
        print(issue, file=sys.stderr)
    if not issues:
        count = len(catalogue(args.root / "docs" / "requirements.csv"))
        print(f"traceability complete: {count} requirements, rtm up to date")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
