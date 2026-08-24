"""SQLAlchemy models + the enums shared by the whole application."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import config
from .database import Base


def utcnow() -> datetime:
    """Naive UTC `datetime` — SQLite has no tz support, so we normalise on write."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ChargeMode(str, enum.Enum):
    """Power profile requested by the driver."""

    ECO = "ECO"           # gentle charge, cheapest amps
    FAST = "FAST"         # as many amps as the site allows
    SOLAR = "SOLAR"       # follow the PV surplus reported by the inverter
    OFF_PEAK = "OFF_PEAK"  # full power, but only outside the peak window


class SessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    REFUNDED = "REFUNDED"


class ConnectorState(str, enum.Enum):
    """IEC 61851 / OCPP flavoured connector lifecycle."""

    AVAILABLE = "AVAILABLE"
    PREPARING = "PREPARING"
    CHARGING = "CHARGING"
    THROTTLED = "THROTTLED"            # charging, but DLB clamped the setpoint
    SUSPENDED_EVSE = "SUSPENDED_EVSE"  # paused by DLB / tariff / solar window
    FINISHING = "FINISHING"
    FAULTED = "FAULTED"                # overload latch tripped


class TariffWindow(str, enum.Enum):
    PEAK = "PEAK"
    OFF_PEAK = "OFF_PEAK"
    SOLAR = "SOLAR"


class EventLevel(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ChargingSession(Base):
    """One plug-in → unplug cycle, with its billing outcome."""

    __tablename__ = "charging_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connector_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    driver_label: Mapped[str] = mapped_column(String(64), default="Guest")

    start_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    mode: Mapped[ChargeMode] = mapped_column(Enum(ChargeMode), default=ChargeMode.ECO)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.ACTIVE, index=True
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING
    )

    # Energy split per tariff window so the invoice can be itemised.
    total_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    peak_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    off_peak_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    solar_kwh: Mapped[float] = mapped_column(Float, default=0.0)

    energy_cost: Mapped[float] = mapped_column(Float, default=0.0)
    service_fee: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default=config.CURRENCY)

    # Snapshot of the meter counter when the session opened (PZEM is cumulative).
    meter_start_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    meter_stop_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)

    avg_power_w: Mapped[float] = mapped_column(Float, default=0.0)
    peak_power_w: Mapped[float] = mapped_column(Float, default=0.0)
    start_soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    samples: Mapped[list["TelemetrySample"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    @property
    def duration_s(self) -> float:
        return ((self.end_time or utcnow()) - self.start_time).total_seconds()


class TariffConfig(Base):
    """Time-of-use price table. The row with the highest id is the active one."""

    __tablename__ = "tariff_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    peak_rate: Mapped[float] = mapped_column(Float, default=config.DEFAULT_PEAK_RATE)
    off_peak_rate: Mapped[float] = mapped_column(Float, default=config.DEFAULT_OFF_PEAK_RATE)
    # Price applied to the share of energy covered by on-site PV generation.
    solar_rate: Mapped[float] = mapped_column(Float, default=config.DEFAULT_SOLAR_RATE)
    # "HH:MM" local time. Windows may wrap midnight (e.g. 22:00 -> 06:00).
    current_peak_start: Mapped[str] = mapped_column(String(5), default=config.DEFAULT_PEAK_START)
    current_peak_end: Mapped[str] = mapped_column(String(5), default=config.DEFAULT_PEAK_END)
    service_fee: Mapped[float] = mapped_column(Float, default=config.DEFAULT_SERVICE_FEE)
    currency: Mapped[str] = mapped_column(String(8), default=config.CURRENCY)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SystemConfig(Base):
    """Single-row table holding the DLB envelope and station defaults."""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    power_limit_w: Mapped[float] = mapped_column(Float, default=config.DEFAULT_POWER_LIMIT_W)
    min_charge_current_a: Mapped[float] = mapped_column(Float, default=config.MIN_CHARGE_CURRENT_A)
    max_charge_current_a: Mapped[float] = mapped_column(Float, default=config.MAX_CHARGE_CURRENT_A)
    eco_current_a: Mapped[float] = mapped_column(Float, default=10.0)
    solar_priority: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TelemetrySample(Base):
    """Down-sampled meter history, used by the dashboard charts."""

    __tablename__ = "telemetry_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("charging_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    connector_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    voltage: Mapped[float] = mapped_column(Float, default=0.0)
    current: Mapped[float] = mapped_column(Float, default=0.0)
    power_w: Mapped[float] = mapped_column(Float, default=0.0)
    energy_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    pv_power_w: Mapped[float] = mapped_column(Float, default=0.0)
    running_cost: Mapped[float] = mapped_column(Float, default=0.0)
    tariff_window: Mapped[str] = mapped_column(String(16), default=TariffWindow.OFF_PEAK.value)

    session: Mapped[ChargingSession | None] = relationship(back_populates="samples")


class SystemEvent(Base):
    """Audit trail for DLB decisions, overload cutoffs and session lifecycle."""

    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[EventLevel] = mapped_column(Enum(EventLevel), default=EventLevel.INFO)
    code: Mapped[str] = mapped_column(String(48), index=True)
    message: Mapped[str] = mapped_column(Text)
    connector_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
