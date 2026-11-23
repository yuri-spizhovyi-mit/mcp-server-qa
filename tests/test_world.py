"""SRV-01: the synthetic world comes only from a seed, the same in every process."""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from energy_mcp.world import GENERIC_SITE_NOUNS, build_world

pytestmark = pytest.mark.req("SRV-01")


def test_same_seed_same_world_and_different_seed_differs() -> None:
    assert build_world(42).digest() == build_world(42).digest() != build_world(43).digest()


def test_world_is_identical_across_processes() -> None:
    """A stdio or HTTP server is a new process with its own hash seed; the world must not care."""
    code = "from energy_mcp.world import build_world; print(build_world(42).digest())"
    digests = set()
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        digests.add(out.stdout.strip())
    assert digests == {build_world(42).digest()}


def test_world_shape() -> None:
    w = build_world()
    assert len(w.meters) == 20 and {m.site_id for m in w.meters} == {f"SITE-{i:02d}" for i in range(1, 7)}
    assert [m.meter_id for m in w.meters] == [f"MTR-{i:04d}" for i in range(1, 21)]
    assert all(len(w.readings[m.meter_id]) == 168 for m in w.meters)
    assert w.readings["MTR-0001"][0].start == "2026-01-05T00:00:00Z"
    assert w.readings["MTR-0001"][-1].start == "2026-01-11T23:00:00Z"
    assert all(r.kwh >= 0 and round(r.kwh, 3) == r.kwh for rs in w.readings.values() for r in rs)
    assert [a.alert_id for a in w.alerts] == [f"ALR-{i:04d}" for i in range(1, 51)]
    assert {a.meter_id for a in w.alerts} <= {m.meter_id for m in w.meters}
    assert {a.status for a in w.alerts} == {"open", "acknowledged"}
    assert all((a.status == "acknowledged") == (a.acknowledged_at is not None) for a in w.alerts)
    assert all((a.acknowledged_by is None) == (a.acknowledged_at is None) for a in w.alerts)


def test_names_are_synthetic() -> None:
    """No real people, companies or places: generic site nouns with a number, technician codes."""
    w = build_world()
    for meter in w.meters:
        match = re.fullmatch(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*) (\d+)", meter.site_name)
        assert match and match.group(1) in GENERIC_SITE_NOUNS, meter.site_name
    assert all(re.fullmatch(r"T-\d{2}", a.acknowledged_by) for a in w.alerts if a.acknowledged_by)


def test_acknowledge_is_idempotent_and_unknown_raises() -> None:
    w = build_world()
    alert = next(a for a in w.alerts if a.status == "open")
    first = w.acknowledge(alert.alert_id, by="op-1", at="2026-01-12T09:00:00Z")
    assert (first.status, first.acknowledged_by, first.acknowledged_at) == ("acknowledged", "op-1", "2026-01-12T09:00:00Z")
    again = w.acknowledge(alert.alert_id, by="op-2", at="2026-01-12T10:00:00Z")
    assert again == first and w.alert(alert.alert_id) == first
    with pytest.raises(KeyError):
        w.acknowledge("ALR-0999", by="op-1", at="2026-01-12T09:00:00Z")
