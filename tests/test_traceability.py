"""ENV-04: every test cites a catalogued requirement, every requirement has a test, the matrix is current.

Each failure the CI gate must catch is planted in a throwaway repo under tmp_path, so the
check is shown to fail on it, and the last test runs the same check on this repo.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import rtm

pytestmark = pytest.mark.req("ENV-04")

CITES_ENV_01 = 'import pytest\n\n@pytest.mark.req("ENV-01")\ndef test_x():\n    pass\n'


def make_repo(tmp_path: Path, rows: list[tuple[str, str]], tests: dict[str, str],
              encoding: str = "utf-8") -> Path:
    """A minimal repo: docs/requirements.csv from (id, risk) rows, and tests/<relpath> files."""
    (tmp_path / "docs").mkdir()
    with (tmp_path / "docs" / "requirements.csv").open("w", encoding=encoding, newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["id", "area", "risk", "requirement"])
        for req_id, risk in rows:
            writer.writerow([req_id, "Environment", risk, "r"])
    for relpath, source in tests.items():
        path = tmp_path / "tests" / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return tmp_path


def test_complete_repo_has_no_problems(tmp_path: Path) -> None:
    root = make_repo(tmp_path, [("ENV-01", "low")], {"test_a.py": CITES_ENV_01})
    rtm.write(root)
    assert rtm.check(root) == []


def test_test_without_req_is_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path, [("ENV-01", "low")],
                     {"test_a.py": "def test_x():\n    pass\n", "test_b.py": CITES_ENV_01})
    rtm.write(root)
    assert "tests/test_a.py::test_x cites no requirement" in rtm.check(root)


def test_uncatalogued_id_is_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path, [("ENV-01", "low")],
                     {"test_a.py": CITES_ENV_01.replace("ENV-01", "ENV-09"), "test_b.py": CITES_ENV_01})
    rtm.write(root)
    assert ("ENV-09 is cited by tests/test_a.py::test_x but not catalogued in docs/requirements.csv"
            in rtm.check(root))


def test_requirement_without_test_is_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path, [("ENV-01", "low"), ("ENV-02", "low")], {"test_a.py": CITES_ENV_01})
    rtm.write(root)
    assert "ENV-02 has no test" in rtm.check(root)


def test_stale_matrix_is_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path, [("ENV-01", "low")], {"test_a.py": CITES_ENV_01})
    rtm.write(root)
    with (root / "docs" / "requirements.csv").open("a", encoding="utf-8", newline="") as f:
        f.write("ENV-02,Environment,low,r\n")
    (root / "tests" / "test_b.py").write_text(CITES_ENV_01.replace("ENV-01", "ENV-02"), encoding="utf-8")
    problems = rtm.check(root)
    assert "docs/rtm.csv is stale: run python scripts/rtm.py" in problems
    assert "docs/rtm.md is stale: run python scripts/rtm.py" in problems
    assert rtm.main(["--check", "--root", str(root)]) == 1


def test_crlf_checkout_is_not_stale(tmp_path: Path) -> None:
    """A Windows runner checks text out with CRLF; that alone must not look like a stale matrix."""
    root = make_repo(tmp_path, [("ENV-01", "low")], {"test_a.py": CITES_ENV_01})
    rtm.write(root)
    for name in ("rtm.md", "rtm.csv"):
        path = root / "docs" / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert rtm.check(root) == []


def test_catalogue_with_bom_is_read(tmp_path: Path) -> None:
    """Excel on Windows saves CSV with a UTF-8 BOM; the first column must still be `id`."""
    root = make_repo(tmp_path, [("ENV-01", "low")], {"test_a.py": CITES_ENV_01}, encoding="utf-8-sig")
    assert rtm.catalogue(root / "docs" / "requirements.csv")[0]["id"] == "ENV-01"
    rtm.write(root)
    assert rtm.check(root) == []


def test_duplicate_and_malformed_ids_are_reported(tmp_path: Path) -> None:
    rows = [("ENV-01", "low"), ("ENV-01", "low"), ("ENV-1", "low"), ("env-02", "low"), ("ENV-03", "urgent")]
    root = make_repo(tmp_path, rows, {"test_a.py": CITES_ENV_01})
    problems = rtm.check(root)
    assert "ENV-01 is catalogued twice" in problems
    assert "ENV-1 is not a valid requirement ID" in problems
    assert "env-02 is not a valid requirement ID" in problems
    assert "ENV-03 has risk 'urgent'; use low, medium or high" in problems


def test_subfolder_class_and_async_tests_are_traced(tmp_path: Path) -> None:
    async_test = 'import pytest\n\n@pytest.mark.req("ENV-01")\nasync def test_y():\n    pass\n'
    class_test = ('import pytest\n\nclass TestC:\n    pytestmark = pytest.mark.req("ENV-01")\n\n'
                  '    def test_z(self):\n        pass\n')
    root = make_repo(tmp_path, [("ENV-01", "low")], {"agent/test_b.py": async_test, "test_c.py": class_test})
    found, untraced = rtm.links(root)
    assert "tests/agent/test_b.py::test_y" in found["ENV-01"]
    assert "tests/test_c.py::TestC::test_z" in found["ENV-01"]
    assert untraced == []


def test_nested_test_classes_are_traced(tmp_path: Path) -> None:
    nested = ('import pytest\n\nclass TestOuter:\n    class TestInner:\n'
              '        @pytest.mark.req("ENV-01")\n        def test_n(self):\n            pass\n')
    root = make_repo(tmp_path, [("ENV-01", "low")], {"test_n.py": nested})
    assert rtm.links(root)[0]["ENV-01"] == ["tests/test_n.py::TestOuter::TestInner::test_n"]


CONFTEST = Path(__file__).with_name("conftest.py")


def _gated_run(pytester: pytest.Pytester, files: dict[str, str]) -> pytest.RunResult:
    """Run pytest in a throwaway repo that uses this repo's collection gate (tests/conftest.py)."""
    pytester.makefile(".ini", pytest="[pytest]\nmarkers =\n    req(id): requirement\n")
    for relpath, source in {"conftest.py": CONFTEST.read_text(encoding="utf-8"), **files}.items():
        path = pytester.path / "tests" / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return pytester.runpytest("tests")


def test_collected_test_without_req_fails_the_run(pytester: pytest.Pytester) -> None:
    result = _gated_run(pytester, {"test_a.py": "def test_x():\n    pass\n"})
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*tests/test_a.py::test_x has no req marker*"])


@pytest.mark.parametrize(("relpath", "source"), [
    ("loop_test.py", CITES_ENV_01),  # pytest's default python_files includes *_test.py
    ("test_a.py", CITES_ENV_01.replace("def test_x", "def testx")),  # pytest's prefix is `test`
], ids=["star_test_file", "test_prefix_without_underscore"])
def test_test_that_rtm_cannot_see_fails_the_run(pytester: pytest.Pytester, relpath: str, source: str) -> None:
    result = _gated_run(pytester, {relpath: source})
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*is collected by pytest but invisible to scripts/rtm.py*"])


def test_this_repo_traceability_is_complete() -> None:
    problems = rtm.check(rtm.ROOT)
    assert problems == [], "\n".join(problems)
