"""The domain records. They double as the tools' output schemas, so field names are a contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Meter(BaseModel):
    meter_id: str
    site_id: str
    site_name: str
    supply: Literal["single-phase", "three-phase"]


class Reading(BaseModel):
    start: str  # UTC hour start, "2026-01-05T00:00:00Z"
    kwh: float  # energy used in that hour, >= 0, 3 decimals


class Alert(BaseModel):
    alert_id: str
    meter_id: str
    severity: Literal["low", "medium", "high"]
    raised_at: str
    status: Literal["open", "acknowledged"]
    note: str
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None


class MeterList(BaseModel):
    meters: list[Meter]


class ReadingPage(BaseModel):
    meter_id: str
    unit: Literal["kWh"] = "kWh"
    readings: list[Reading]
    next_cursor: str | None = None


class AlertList(BaseModel):
    alerts: list[Alert]
