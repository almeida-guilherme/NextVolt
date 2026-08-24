"""Synchronous persistence helpers.

Every function here takes an explicit `Session` and does *only* DB work, so the
async layer can hand it to a worker thread with `run_in_threadpool` and never
block the event loop that is driving the WebSockets.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .billing_service import CostAccumulator, Invoice, TariffSnapshot
from .models import (
    ChargeMode,
    ChargingSession,
    EventLevel,
    PaymentStatus,
    SessionStatus,
    SystemConfig,
    SystemEvent,
    TariffConfig,
    TelemetrySample,
    utcnow,
)


# ----------------------------------------------------------------- config rows
def get_tariff(db: Session) -> TariffConfig:
    """Active tariff = newest row. Seeds a default one on first boot."""
    row = db.execute(select(TariffConfig).order_by(TariffConfig.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        row = TariffConfig()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_tariff(db: Session, **fields) -> TariffConfig:
    row = get_tariff(db)
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def get_system_config(db: Session) -> SystemConfig:
    row = db.execute(select(SystemConfig).order_by(SystemConfig.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        row = SystemConfig()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_system_config(db: Session, **fields) -> SystemConfig:
    row = get_system_config(db)
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------- events
def log_event(
    db: Session,
    level: EventLevel,
    code: str,
    message: str,
    connector_id: int | None = None,
) -> SystemEvent:
    event = SystemEvent(level=level, code=code, message=message, connector_id=connector_id)
    db.add(event)
    db.commit()
    return event


def recent_events(db: Session, limit: int = 50) -> list[SystemEvent]:
    return list(
        db.execute(
            select(SystemEvent).order_by(SystemEvent.id.desc()).limit(min(limit, 500))
        ).scalars()
    )


# ------------------------------------------------------------------- sessions
def active_session_for(db: Session, connector_id: int) -> ChargingSession | None:
    return db.execute(
        select(ChargingSession)
        .where(
            ChargingSession.connector_id == connector_id,
            ChargingSession.status == SessionStatus.ACTIVE,
        )
        .order_by(ChargingSession.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def open_session(
    db: Session,
    connector_id: int,
    mode: ChargeMode,
    driver_label: str,
    tariff: TariffSnapshot,
    meter_start_kwh: float | None,
    start_soc: float | None,
) -> ChargingSession:
    row = ChargingSession(
        connector_id=connector_id,
        mode=mode,
        driver_label=driver_label or "Guest",
        status=SessionStatus.ACTIVE,
        payment_status=PaymentStatus.PENDING,
        meter_start_kwh=meter_start_kwh,
        start_soc=start_soc,
        currency=tariff.currency,
        service_fee=0.0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def flush_progress(
    db: Session,
    session_id: int,
    acc: CostAccumulator,
    running_cost: float,
    soc: float | None,
    meter_kwh: float | None,
) -> None:
    """Periodic checkpoint so a crash never loses more than one interval."""
    row = db.get(ChargingSession, session_id)
    if row is None or row.status is not SessionStatus.ACTIVE:
        return
    row.total_kwh = acc.total_kwh
    row.peak_kwh = acc.peak_kwh
    row.off_peak_kwh = acc.off_peak_kwh
    row.solar_kwh = acc.solar_kwh
    row.energy_cost = acc.energy_cost
    row.total_cost = running_cost
    row.avg_power_w = acc.avg_power_w
    row.peak_power_w = acc.peak_power_w
    row.meter_stop_kwh = meter_kwh
    if soc is not None:
        row.end_soc = soc
    db.commit()


def close_session(
    db: Session,
    session_id: int,
    invoice: Invoice,
    acc: CostAccumulator,
    meter_stop_kwh: float | None,
    end_soc: float | None,
    reason: str,
    status: SessionStatus = SessionStatus.COMPLETED,
    payment_status: PaymentStatus | None = None,
) -> ChargingSession | None:
    row = db.get(ChargingSession, session_id)
    if row is None:
        return None
    row.end_time = utcnow()
    row.status = status
    row.total_kwh = invoice.total_kwh
    row.peak_kwh = invoice.peak_kwh
    row.off_peak_kwh = invoice.off_peak_kwh
    row.solar_kwh = invoice.solar_kwh
    row.energy_cost = invoice.energy_cost
    row.service_fee = invoice.service_fee
    row.total_cost = invoice.total_cost
    row.currency = invoice.currency
    row.avg_power_w = acc.avg_power_w
    row.peak_power_w = acc.peak_power_w
    row.meter_stop_kwh = meter_stop_kwh
    row.stop_reason = reason
    if end_soc is not None:
        row.end_soc = end_soc
    if payment_status is not None:
        row.payment_status = payment_status
    db.commit()
    db.refresh(row)
    return row


def abort_stale_session(
    db: Session, session_id: int, service_fee: float, reason: str
) -> ChargingSession | None:
    """Close a session that survived an unclean shutdown.

    The in-memory accumulator is gone, so we bill the last checkpoint written by
    `flush_progress` (at most `TELEMETRY_PERSIST_INTERVAL_S` of energy is lost)
    rather than zeroing the session — the driver did consume that energy.
    """
    row = db.get(ChargingSession, session_id)
    if row is None:
        return None
    row.end_time = utcnow()
    row.status = SessionStatus.ABORTED
    row.stop_reason = reason
    row.service_fee = service_fee if row.total_kwh > 0 else 0.0
    row.total_cost = round(row.energy_cost + row.service_fee, 2)
    db.commit()
    db.refresh(row)
    return row


def set_payment_status(db: Session, session_id: int, status: PaymentStatus) -> ChargingSession | None:
    row = db.get(ChargingSession, session_id)
    if row is None:
        return None
    row.payment_status = status
    db.commit()
    db.refresh(row)
    return row


def history(
    db: Session,
    limit: int = 25,
    offset: int = 0,
    status: SessionStatus | None = None,
    connector_id: int | None = None,
    include_active: bool = False,
) -> tuple[int, list[ChargingSession]]:
    """Closed sessions by default — an in-flight session has no final invoice yet,
    so listing it beside billed ones (and outside `history_aggregate`) misleads."""
    filters = []
    if status is not None:
        filters.append(ChargingSession.status == status)
    elif not include_active:
        filters.append(ChargingSession.status != SessionStatus.ACTIVE)
    if connector_id is not None:
        filters.append(ChargingSession.connector_id == connector_id)

    total = db.execute(
        select(func.count()).select_from(ChargingSession).where(*filters)
    ).scalar_one()
    rows = list(
        db.execute(
            select(ChargingSession)
            .where(*filters)
            .order_by(ChargingSession.id.desc())
            .limit(min(limit, 200))
            .offset(max(offset, 0))
        ).scalars()
    )
    return total, rows


def history_aggregate(db: Session) -> dict:
    row = db.execute(
        select(
            func.count(ChargingSession.id),
            func.coalesce(func.sum(ChargingSession.total_kwh), 0.0),
            func.coalesce(func.sum(ChargingSession.total_cost), 0.0),
            func.coalesce(func.sum(ChargingSession.solar_kwh), 0.0),
            func.coalesce(func.sum(ChargingSession.peak_kwh), 0.0),
        ).where(ChargingSession.status != SessionStatus.ACTIVE)
    ).one()
    count, kwh, cost, solar_kwh, peak_kwh = row
    unpaid = db.execute(
        select(func.coalesce(func.sum(ChargingSession.total_cost), 0.0)).where(
            ChargingSession.payment_status == PaymentStatus.PENDING,
            ChargingSession.status != SessionStatus.ACTIVE,
        )
    ).scalar_one()
    return {
        "sessions": int(count or 0),
        "total_kwh": round(float(kwh or 0.0), 3),
        "total_revenue": round(float(cost or 0.0), 2),
        "solar_kwh": round(float(solar_kwh or 0.0), 3),
        "peak_kwh": round(float(peak_kwh or 0.0), 3),
        "outstanding": round(float(unpaid or 0.0), 2),
        "avg_ticket": round(float(cost or 0.0) / count, 2) if count else 0.0,
        "currency": config.CURRENCY,
    }


def session_to_dict(row: ChargingSession) -> dict:
    return {
        "id": row.id,
        "connector_id": row.connector_id,
        "driver_label": row.driver_label,
        "start_time": row.start_time.isoformat() + "Z",
        "end_time": row.end_time.isoformat() + "Z" if row.end_time else None,
        "mode": row.mode.value,
        "status": row.status.value,
        "payment_status": row.payment_status.value,
        "total_kwh": round(row.total_kwh, 4),
        "peak_kwh": round(row.peak_kwh, 4),
        "off_peak_kwh": round(row.off_peak_kwh, 4),
        "solar_kwh": round(row.solar_kwh, 4),
        "energy_cost": round(row.energy_cost, 3),
        "service_fee": round(row.service_fee, 2),
        "total_cost": round(row.total_cost, 2),
        "currency": row.currency,
        "avg_power_w": round(row.avg_power_w, 1),
        "peak_power_w": round(row.peak_power_w, 1),
        "start_soc": row.start_soc,
        "end_soc": row.end_soc,
        "stop_reason": row.stop_reason,
        "duration_s": round(row.duration_s, 1),
    }


# ------------------------------------------------------------------- samples
def insert_samples(db: Session, rows: list[dict]) -> None:
    if not rows:
        return
    db.add_all([TelemetrySample(**row) for row in rows])
    db.commit()


def samples_for_chart(
    db: Session,
    minutes: int = 30,
    session_id: int | None = None,
    limit: int = 720,
) -> list[dict]:
    filters = []
    if session_id is not None:
        filters.append(TelemetrySample.session_id == session_id)
    else:
        filters.append(TelemetrySample.recorded_at >= utcnow() - timedelta(minutes=minutes))

    rows = list(
        db.execute(
            select(TelemetrySample)
            .where(*filters)
            .order_by(TelemetrySample.id.desc())
            .limit(min(limit, 5000))
        ).scalars()
    )
    rows.reverse()
    return [
        {
            "at": row.recorded_at.isoformat() + "Z",
            "connector_id": row.connector_id,
            "session_id": row.session_id,
            "voltage": round(row.voltage, 1),
            "current": round(row.current, 2),
            "power_w": round(row.power_w, 1),
            "power_kw": round(row.power_w / 1000.0, 3),
            "energy_kwh": round(row.energy_kwh, 4),
            "soc": row.soc,
            "pv_power_w": round(row.pv_power_w, 1),
            "running_cost": round(row.running_cost, 3),
            "tariff_window": row.tariff_window,
        }
        for row in rows
    ]


def purge_old_samples(db: Session, keep_days: int = 3) -> int:
    cutoff: datetime = utcnow() - timedelta(days=keep_days)
    result = db.query(TelemetrySample).filter(TelemetrySample.recorded_at < cutoff).delete()
    db.commit()
    return int(result or 0)
