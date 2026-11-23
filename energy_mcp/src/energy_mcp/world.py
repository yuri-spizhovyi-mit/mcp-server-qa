"""The synthetic world: sites, meters, hourly readings and alerts, built only from a seed.

Everything is drawn from one `random.Random(seed)` in a fixed order, and nothing reads the
wall clock, so the same seed gives the same world in any process (the tests check this
across hash seeds). Names are synthetic: generic site nouns with a number, technician codes.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from energy_mcp.models import Alert, Meter, Reading

WINDOW_START = "2026-01-05T00:00:00Z"
HOURS = 168
METER_COUNT = 20
ALERT_COUNT = 50
SITE_COUNT = 6
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

GENERIC_SITE_NOUNS = (
    "Pump House", "Cold Store", "Workshop", "Office Block", "Warehouse", "Boiler Room", "Water Tower", "Depot",
)
# Share of a meter's base load used in each hour of the day (0 = midnight).
DAILY_SHAPE = (
    0.55, 0.5, 0.48, 0.47, 0.48, 0.55, 0.7, 0.9, 1.05, 1.15, 1.2, 1.2,
    1.15, 1.15, 1.2, 1.2, 1.25, 1.35, 1.4, 1.3, 1.1, 0.9, 0.75, 0.62,
)
NOTE_TEMPLATES = (
    "Consumption on {meter} rose above its usual band; checked the panel, no fault found.",
    "{meter} reported a gap of two readings; the logger restarted on its own.",
    "Night load on {meter} is higher than last week; possible heater left on.",
    "Voltage dip recorded at {meter}; supply returned to normal within the hour.",
    "{meter} reading flat for three hours; check the pulse cable on the next visit.",
    "Spike on {meter} matches the scheduled pump test; no action needed.",
)
SEVERITIES = ("low", "medium", "high")


def _at(hour: int) -> str:
    start = datetime.strptime(WINDOW_START, TIME_FORMAT).replace(tzinfo=UTC)
    return (start + timedelta(hours=hour)).strftime(TIME_FORMAT)


@dataclass
class World:
    seed: int
    meters: list[Meter]
    readings: dict[str, list[Reading]]
    alerts: list[Alert]
    _index: dict[str, int] = field(init=False, repr=False)
    _lock: threading.Lock = field(init=False, repr=False, compare=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._index = {a.alert_id: i for i, a in enumerate(self.alerts)}

    def digest(self) -> str:
        """sha256 of the whole world as canonical JSON."""
        data = {
            "seed": self.seed,
            "meters": [m.model_dump(mode="json") for m in self.meters],
            "readings": {k: [r.model_dump(mode="json") for r in v] for k, v in self.readings.items()},
            "alerts": [a.model_dump(mode="json") for a in self.alerts],
        }
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def alert(self, alert_id: str) -> Alert | None:
        i = self._index.get(alert_id)
        return None if i is None else self.alerts[i]

    def acknowledge(self, alert_id: str, by: str, at: str) -> Alert:
        """Acknowledge an alert; a second call returns the first acknowledgement unchanged (SRV-D3)."""
        i = self._index[alert_id]  # KeyError for an unknown alert
        with self._lock:  # sync tools run in worker threads: read and write as one step
            alert = self.alerts[i]
            if alert.status == "acknowledged":
                return alert
            self.alerts[i] = alert.model_copy(
                update={"status": "acknowledged", "acknowledged_by": by, "acknowledged_at": at})
            return self.alerts[i]


def build_world(seed: int = 42) -> World:
    rng = random.Random(seed)
    nouns = rng.sample(GENERIC_SITE_NOUNS, SITE_COUNT)
    sites = [(f"SITE-{i:02d}", f"{noun} {rng.randint(1, 9)}") for i, noun in enumerate(nouns, start=1)]

    meters = []
    for n in range(1, METER_COUNT + 1):
        site_id, site_name = sites[n - 1] if n <= SITE_COUNT else rng.choice(sites)
        supply = rng.choice(("single-phase", "three-phase"))
        meters.append(Meter(meter_id=f"MTR-{n:04d}", site_id=site_id, site_name=site_name, supply=supply))

    readings = {}
    for meter in meters:
        base = rng.uniform(0.5, 5.0)
        series = []
        for hour in range(HOURS):
            kwh = base * DAILY_SHAPE[hour % 24] + rng.gauss(0, 0.1 * base)
            series.append(Reading(start=_at(hour), kwh=max(0.0, round(kwh, 3))))
        readings[meter.meter_id] = series

    alerts = []
    for n in range(1, ALERT_COUNT + 1):
        meter_id = rng.choice(meters).meter_id
        severity = rng.choices(SEVERITIES, weights=(5, 3, 2))[0]
        raised = rng.randrange(HOURS)
        note = rng.choice(NOTE_TEMPLATES).format(meter=meter_id)
        alert = Alert(alert_id=f"ALR-{n:04d}", meter_id=meter_id, severity=severity, raised_at=_at(raised),
                      status="open", note=note)
        if rng.random() >= 0.7:
            alert = alert.model_copy(update={
                "status": "acknowledged",
                "acknowledged_by": f"T-{rng.randint(1, 12):02d}",
                "acknowledged_at": _at(min(raised + rng.randint(1, 24), HOURS - 1)),
            })
        alerts.append(alert)
    return World(seed=seed, meters=meters, readings=readings, alerts=alerts)
