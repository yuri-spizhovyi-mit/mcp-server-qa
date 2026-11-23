"""The collection gate (ENV-04): pytest itself refuses a run with a test the matrix can't see.

`scripts/rtm.py` builds the matrix from the tests' source, which is a model of what pytest
collects. This hook checks that model against what pytest really collected, so a test that
has no `req` marker, or that rtm can't see (a `*_test.py` file, a `testx` function), stops the
run instead of slipping past the matrix.
"""

from __future__ import annotations

import pytest

import rtm

pytest_plugins = ["pytester"]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    found, untraced = rtm.links(config.rootpath)
    visible = {test for tests in found.values() for test in tests} | set(untraced)
    problems = set()
    for item in items:
        test_id = item.nodeid.split("[", 1)[0]  # a parametrized test counts once
        if not any(mark.args for mark in item.iter_markers("req")):
            problems.add(f"{test_id} has no req marker")
        if test_id not in visible:
            problems.add(f"{test_id} is collected by pytest but invisible to scripts/rtm.py")
    if problems:
        raise pytest.UsageError("traceability gate (ENV-04):\n" + "\n".join(sorted(problems)))


@pytest.fixture(scope="session")
def issuer():
    """One test issuer (and RSA key pair) per test session; see tests/issuer.py."""
    from issuer import Issuer

    return Issuer()
