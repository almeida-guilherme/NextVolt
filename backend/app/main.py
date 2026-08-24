"""FastAPI application: REST control plane + `/ws/telemetry` real-time plane."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import config, session_service as svc
from .database import get_db, init_db
from .models import ChargeMode, ChargingSession, SessionStatus
from .orchestrator import controller
from .schemas import (
    HistoryResponse,
    ModeRequest,
    PaymentRequest,
    PowerLimitRequest,
    SimpleOk,
    StartSessionRequest,
    StopSessionRequest,
    TariffRequest,
)
from .station_state import station
from .ws_manager import Role, manager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s"
)
logger = logging.getLogger("goodwe.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await controller.bootstrap()
    controller.start()
    logger.info(
        "GoodWe station controller ready — %d connectors, limit %.0f W",
        len(station.connectors),
        station.power_limit_w,
    )
    try:
        yield
    finally:
        await controller.stop()


app = FastAPI(
    title="GoodWe Smart EV Charging Station",
    description=(
        "Dynamic Load Balancing, time-of-use billing and real-time telemetry "
        "for a PZEM-004T instrumented EV charging station."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in config.CORS_ORIGINS if origin.strip()],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================== system / state
@app.get("/api/health", tags=["system"])
async def health() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "station_id": station.station_id,
        "station_online": station.online,
        "tick": station.tick,
        "dashboards": manager.count(Role.DASHBOARD),
        "stations": manager.count(Role.STATION),
    }


@app.get("/api/state", tags=["system"])
async def get_state() -> dict:
    """Same payload the WebSocket pushes — handy for a cold page load."""
    return station.snapshot()


@app.get("/api/config", tags=["config"])
async def get_config(db: Session = Depends(get_db)) -> dict:
    cfg = svc.get_system_config(db)
    tariff = svc.get_tariff(db)
    return {
        "power_limit_w": cfg.power_limit_w,
        "min_current_a": cfg.min_charge_current_a,
        "max_current_a": cfg.max_charge_current_a,
        "eco_current_a": cfg.eco_current_a,
        "solar_priority": cfg.solar_priority,
        "nominal_voltage": config.NOMINAL_VOLTAGE,
        "phases": config.PHASES,
        "connector_count": len(station.connectors),
        "modes": [mode.value for mode in ChargeMode],
        "tariff": {
            "peak_rate": tariff.peak_rate,
            "off_peak_rate": tariff.off_peak_rate,
            "solar_rate": tariff.solar_rate,
            "current_peak_start": tariff.current_peak_start,
            "current_peak_end": tariff.current_peak_end,
            "service_fee": tariff.service_fee,
            "currency": tariff.currency,
        },
    }


@app.post("/api/config/power-limit", tags=["config"])
async def set_power_limit(payload: PowerLimitRequest) -> dict:
    """Update the Dynamic Load Balancing envelope (takes effect next tick)."""
    return await controller.set_power_limit(
        power_limit_w=payload.power_limit_w,
        eco_current_a=payload.eco_current_a,
        max_current_a=payload.max_current_a,
        min_current_a=payload.min_current_a,
        solar_priority=payload.solar_priority,
    )


@app.post("/api/config/tariff", tags=["config"])
async def set_tariff(payload: TariffRequest) -> dict:
    return await controller.set_tariff(
        peak_rate=payload.peak_rate,
        off_peak_rate=payload.off_peak_rate,
        solar_rate=payload.solar_rate,
        current_peak_start=payload.current_peak_start,
        current_peak_end=payload.current_peak_end,
        service_fee=payload.service_fee,
        currency=payload.currency,
    )


@app.post("/api/system/reset-overload", tags=["system"], response_model=SimpleOk)
async def reset_overload() -> SimpleOk:
    await controller.reset_overload()
    return SimpleOk(message="Overload latch cleared")


# ==================================================================== sessions
@app.post("/api/sessions/start", tags=["sessions"], status_code=201)
async def start_session(payload: StartSessionRequest) -> dict:
    try:
        return await controller.start_session(
            connector_id=payload.connector_id,
            mode=payload.mode,
            driver_label=payload.driver_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/sessions/stop", tags=["sessions"])
async def stop_session(payload: StopSessionRequest = Body(default=StopSessionRequest())) -> dict:
    try:
        return await controller.stop_session(
            connector_id=payload.connector_id,
            session_id=payload.session_id,
            reason=payload.reason,
            mark_paid=payload.mark_paid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/sessions/history", tags=["sessions"], response_model=HistoryResponse)
async def session_history(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: SessionStatus | None = None,
    connector_id: int | None = Query(default=None, ge=1, le=8),
    include_active: bool = Query(
        default=False, description="Include the session currently in progress"
    ),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    total, rows = svc.history(
        db,
        limit=limit,
        offset=offset,
        status=status,
        connector_id=connector_id,
        include_active=include_active,
    )
    return HistoryResponse(
        total=total,
        items=[svc.session_to_dict(row) for row in rows],
        aggregate=svc.history_aggregate(db),
    )


@app.get("/api/sessions/active", tags=["sessions"])
async def active_sessions() -> dict:
    return {
        "items": [
            connector.to_dict()
            for connector in station.connectors.values()
            if connector.session_active
        ]
    }


@app.get("/api/sessions/{session_id}", tags=["sessions"])
async def get_session(session_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ChargingSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session #{session_id} not found")
    return {
        "session": svc.session_to_dict(row),
        "samples": svc.samples_for_chart(db, session_id=session_id),
    }


@app.post("/api/sessions/{session_id}/payment", tags=["sessions"])
async def pay_session(
    session_id: int, payload: PaymentRequest, db: Session = Depends(get_db)
) -> dict:
    row = svc.set_payment_status(db, session_id, payload.payment_status)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session #{session_id} not found")
    return svc.session_to_dict(row)


# ================================================================== connectors
@app.post("/api/connectors/mode", tags=["connectors"])
async def set_mode(payload: ModeRequest) -> dict:
    if payload.connector_id not in station.connectors:
        raise HTTPException(status_code=404, detail=f"Unknown connector {payload.connector_id}")
    return await controller.set_mode(payload.connector_id, payload.mode)


# =================================================================== telemetry
@app.get("/api/telemetry/history", tags=["telemetry"])
async def telemetry_history(
    minutes: int = Query(default=30, ge=1, le=1440),
    session_id: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return {"items": svc.samples_for_chart(db, minutes=minutes, session_id=session_id)}


@app.post("/api/telemetry", tags=["telemetry"])
async def ingest_telemetry(frame: dict = Body(...)) -> dict:
    """HTTP fallback for firmware that cannot hold a WebSocket open."""
    station.ingest(frame)
    return station.control_frame()


@app.get("/api/events", tags=["telemetry"])
async def events(
    limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)
) -> dict:
    return {
        "items": [
            {
                "id": row.id,
                "at": row.created_at.isoformat() + "Z",
                "level": row.level.value,
                "code": row.code,
                "message": row.message,
                "connector_id": row.connector_id,
            }
            for row in svc.recent_events(db, limit)
        ]
    }


# =================================================================== WebSocket
@app.websocket("/ws/telemetry")
async def ws_telemetry(
    websocket: WebSocket,
    role: str = Query(default="dashboard", description="'station' or 'dashboard'"),
) -> None:
    """Bidirectional real-time channel.

    * `?role=station`   — send `{"type":"telemetry", ...}` frames, receive
                          `{"type":"control", ...}` setpoints back.
    * `?role=dashboard` — receive the aggregated `{"type":"state"}` snapshot at
                          the control-loop rate (1 Hz) and on every command.
    """
    peer_role = Role.parse(role)
    await manager.connect(websocket, peer_role)
    await manager.send_to(
        websocket,
        {
            "type": "welcome",
            "role": peer_role.value,
            "station_id": station.station_id,
            "control_interval_s": config.CONTROL_INTERVAL_S,
            "nominal_voltage": config.NOMINAL_VOLTAGE,
            "connector_ids": sorted(station.connectors),
        },
    )
    if peer_role is Role.DASHBOARD:
        await manager.send_to(websocket, station.snapshot())
    else:
        await manager.send_to(websocket, station.control_frame())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to(websocket, {"type": "error", "detail": "invalid JSON"})
                continue
            if not isinstance(message, dict):
                continue

            kind = str(message.get("type", "telemetry")).lower()
            if kind == "ping":
                await manager.send_to(websocket, {"type": "pong", "tick": station.tick})
            elif kind == "telemetry":
                station.ingest(message)
                if peer_role is Role.STATION and message.get("ack"):
                    await manager.send_to(websocket, station.control_frame())
            elif kind == "subscribe":
                await manager.send_to(websocket, station.snapshot())
            else:
                await manager.send_to(
                    websocket, {"type": "error", "detail": f"unsupported type '{kind}'"}
                )
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - transport level noise
        logger.debug("websocket loop ended", exc_info=True)
    finally:
        await manager.disconnect(websocket)


# ============================================================ static dashboard
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.is_dir():  # `npm run build` output — one-process demo deployment
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="dashboard")
else:

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "GoodWe Smart EV Charging Station API",
                "docs": "/docs",
                "state": "/api/state",
                "websocket": "/ws/telemetry?role=dashboard",
                "hint": "Run the Vite dev server in /frontend for the dashboard.",
            }
        )
