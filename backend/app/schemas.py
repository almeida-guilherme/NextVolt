"""Pydantic request/response contracts for the REST layer."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ChargeMode, PaymentStatus, SessionStatus

HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class StartSessionRequest(BaseModel):
    connector_id: int = Field(default=1, ge=1, le=8)
    mode: ChargeMode = ChargeMode.ECO
    driver_label: str = Field(default="Guest", max_length=64)
    target_soc: float | None = Field(default=None, ge=0, le=100)


class StopSessionRequest(BaseModel):
    connector_id: int | None = Field(default=None, ge=1, le=8)
    session_id: int | None = Field(default=None, ge=1)
    reason: str = Field(default="OPERATOR_STOP", max_length=64)
    mark_paid: bool = False


class ModeRequest(BaseModel):
    connector_id: int = Field(default=1, ge=1, le=8)
    mode: ChargeMode


class PowerLimitRequest(BaseModel):
    """`POST /api/config/power-limit` — the DLB envelope."""

    power_limit_w: float = Field(ge=500, le=100_000)
    eco_current_a: float | None = Field(default=None, ge=6, le=80)
    max_current_a: float | None = Field(default=None, ge=6, le=80)
    min_current_a: float | None = Field(default=None, ge=6, le=32)
    solar_priority: bool | None = None


class TariffRequest(BaseModel):
    peak_rate: float = Field(ge=0, le=100)
    off_peak_rate: float = Field(ge=0, le=100)
    solar_rate: float | None = Field(default=None, ge=0, le=100)
    current_peak_start: str = Field(default="18:00")
    current_peak_end: str = Field(default="21:00")
    service_fee: float | None = Field(default=None, ge=0, le=1000)
    currency: str | None = Field(default=None, max_length=8)

    @field_validator("current_peak_start", "current_peak_end")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        if not HHMM.match(value.strip()):
            raise ValueError("expected HH:MM in 24h format")
        return value.strip()


class PaymentRequest(BaseModel):
    payment_status: PaymentStatus = PaymentStatus.PAID


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connector_id: int
    driver_label: str
    start_time: datetime
    end_time: datetime | None
    mode: ChargeMode
    status: SessionStatus
    payment_status: PaymentStatus
    total_kwh: float
    peak_kwh: float
    off_peak_kwh: float
    solar_kwh: float
    energy_cost: float
    service_fee: float
    total_cost: float
    currency: str
    avg_power_w: float
    peak_power_w: float
    start_soc: float | None
    end_soc: float | None
    stop_reason: str | None
    duration_s: float


class HistoryResponse(BaseModel):
    total: int
    items: list[SessionOut]
    aggregate: dict


class SimpleOk(BaseModel):
    ok: bool = True
    message: str = ""
