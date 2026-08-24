"""In-memory mirror of the physical station.

Telemetry arrives at whatever rate the ESP32 feels like (5-10 Hz), while the
control loop runs at a fixed 1 Hz and the dashboard is fed from a single
serialised snapshot. Keeping one authoritative in-memory object in the middle
decouples all three and keeps SQLite writes off the hot path.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from . import config
from .billing_service import BillingService, CostAccumulator, TariffSnapshot
from .models import ChargeMode, ConnectorState, EventLevel, TariffWindow, utcnow
from .power_manager import ConnectorRequest, Decision, SiteContext


@dataclass
class ConnectorRuntime:
    """Live state of a single connector (meter + session + DLB verdict)."""

    connector_id: int
    mode: ChargeMode = ChargeMode.ECO

    # --- session ---
    session_id: int | None = None
    driver_label: str = "Guest"
    started_at: datetime | None = None
    acc: CostAccumulator = field(default_factory=CostAccumulator)
    running_cost: float = 0.0
    quote: dict = field(default_factory=dict)

    # --- meter (as reported by PZEM-004T / simulator) ---
    voltage: float = 0.0
    current: float = 0.0
    power_w: float = 0.0
    energy_kwh: float = 0.0        # cumulative lifetime counter
    frequency: float = 0.0
    power_factor: float = 0.0
    temperature_c: float | None = None

    # --- vehicle ---
    vehicle_connected: bool = False
    soc: float | None = None
    battery_capacity_kwh: float | None = None

    # --- control output ---
    setpoint_a: float = 0.0
    allocated_w: float = 0.0
    relay_closed: bool = False
    state: ConnectorState = ConnectorState.AVAILABLE
    reason: str = "IDLE"
    throttled: bool = False

    # --- bookkeeping ---
    meter_start_kwh: float | None = None
    last_meter_kwh: float | None = None
    last_tick_monotonic: float | None = None

    @property
    def session_active(self) -> bool:
        return self.session_id is not None

    def open_session(self, session_id: int, mode: ChargeMode, driver_label: str) -> None:
        self.session_id = session_id
        self.mode = mode
        self.driver_label = driver_label or "Guest"
        self.started_at = utcnow()
        self.acc = CostAccumulator()
        self.running_cost = 0.0
        self.meter_start_kwh = self.energy_kwh or None
        self.last_meter_kwh = self.energy_kwh or None
        self.last_tick_monotonic = None

    def close_session(self) -> None:
        self.session_id = None
        self.started_at = None
        self.setpoint_a = 0.0
        self.allocated_w = 0.0
        self.relay_closed = False
        self.throttled = False
        self.state = (
            ConnectorState.PREPARING if self.vehicle_connected else ConnectorState.AVAILABLE
        )
        self.reason = "NO_ACTIVE_SESSION"
        self.running_cost = 0.0
        self.last_tick_monotonic = None

    def to_request(self) -> ConnectorRequest:
        return ConnectorRequest(
            connector_id=self.connector_id,
            mode=self.mode,
            session_active=self.session_active,
            vehicle_connected=self.vehicle_connected,
            soc=self.soc,
            started_at=self.started_at,
        )

    def to_dict(self) -> dict:
        return {
            "connector_id": self.connector_id,
            "mode": self.mode.value,
            "state": self.state.value,
            "reason": self.reason,
            "throttled": self.throttled,
            "session_id": self.session_id,
            "driver_label": self.driver_label,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "voltage": round(self.voltage, 1),
            "current": round(self.current, 2),
            "power_w": round(self.power_w, 1),
            "power_kw": round(self.power_w / 1000.0, 3),
            "energy_kwh": round(self.energy_kwh, 3),
            "frequency": round(self.frequency, 2),
            "power_factor": round(self.power_factor, 3),
            "temperature_c": self.temperature_c,
            "vehicle_connected": self.vehicle_connected,
            "soc": round(self.soc, 1) if self.soc is not None else None,
            "battery_capacity_kwh": self.battery_capacity_kwh,
            "setpoint_a": round(self.setpoint_a, 2),
            "allocated_w": round(self.allocated_w, 1),
            "relay_closed": self.relay_closed,
            "session_kwh": round(self.acc.total_kwh, 4),
            "session_cost": round(self.running_cost, 3),
            "billing": self.acc.to_dict(),
            "quote": self.quote,
        }


class StationState:
    """Aggregate: connectors + inverter feed + operator configuration."""

    def __init__(self, connector_count: int = config.CONNECTOR_COUNT) -> None:
        self.station_id = config.STATION_ID
        self.connectors: dict[int, ConnectorRuntime] = {
            cid: ConnectorRuntime(connector_id=cid) for cid in range(1, connector_count + 1)
        }

        # Inverter / house feed (GoodWe hybrid inverter or the simulator).
        self.pv_power_w: float = 0.0
        self.house_load_w: float = 0.0
        self.grid_power_w: float = 0.0
        self.battery_soc: float | None = None

        # Operator configuration (mirrored from SystemConfig).
        self.power_limit_w: float = config.DEFAULT_POWER_LIMIT_W
        self.min_current_a: float = config.MIN_CHARGE_CURRENT_A
        self.max_current_a: float = config.MAX_CHARGE_CURRENT_A
        self.eco_current_a: float = 10.0
        self.solar_priority: bool = True

        self.tariff: TariffSnapshot = TariffSnapshot()
        self.billing = BillingService(self.tariff)

        self.last_frame_monotonic: float | None = None
        self.last_frame_at: datetime | None = None
        self.frames_received: int = 0
        self.firmware: str | None = None

        self.decision: Decision | None = None
        self.events: deque[dict] = deque(maxlen=config.EVENT_BUFFER_SIZE)
        self.tick = 0

    # ------------------------------------------------------------ accessors
    def connector(self, connector_id: int) -> ConnectorRuntime:
        if connector_id not in self.connectors:
            self.connectors[connector_id] = ConnectorRuntime(connector_id=connector_id)
        return self.connectors[connector_id]

    @property
    def online(self) -> bool:
        if self.last_frame_monotonic is None:
            return False
        return (time.monotonic() - self.last_frame_monotonic) <= config.STATION_TIMEOUT_S

    @property
    def total_power_w(self) -> float:
        return sum(c.power_w for c in self.connectors.values())

    @property
    def measured_total_w(self) -> float:
        """Everything behind the main breaker: EVSE + rest of the building."""
        return self.total_power_w + max(self.house_load_w, 0.0)

    @property
    def solar_share(self) -> float:
        """Fraction of the *EVSE* load currently covered by on-site PV."""
        evse = self.total_power_w
        if evse <= 0:
            return 0.0
        surplus = max(self.pv_power_w - max(self.house_load_w, 0.0), 0.0)
        return min(surplus / evse, 1.0)

    def set_tariff(self, tariff: TariffSnapshot) -> None:
        self.tariff = tariff
        self.billing = BillingService(tariff)

    def site_context(self) -> SiteContext:
        reference = next(
            (c.voltage for c in self.connectors.values() if c.voltage > 90.0),
            config.NOMINAL_VOLTAGE,
        )
        return SiteContext(
            power_limit_w=self.power_limit_w,
            voltage=reference,
            phases=config.PHASES,
            min_current_a=self.min_current_a,
            max_current_a=self.max_current_a,
            eco_current_a=self.eco_current_a,
            solar_priority=self.solar_priority,
            pv_power_w=self.pv_power_w,
            house_load_w=self.house_load_w,
            measured_total_w=self.measured_total_w,
            is_peak=self.billing.is_peak(),
        )

    # -------------------------------------------------------------- ingestion
    def ingest(self, frame: dict) -> None:
        """Apply one telemetry frame coming from the station/simulator."""
        self.last_frame_monotonic = time.monotonic()
        self.last_frame_at = utcnow()
        self.frames_received += 1

        if isinstance(frame.get("station_id"), str):
            self.station_id = frame["station_id"]
        if isinstance(frame.get("firmware"), str):
            self.firmware = frame["firmware"]

        solar = frame.get("solar") or frame.get("inverter") or {}
        if isinstance(solar, dict):
            self.pv_power_w = _as_float(solar.get("pv_power_w"), self.pv_power_w)
            self.house_load_w = _as_float(solar.get("house_load_w"), self.house_load_w)
            self.grid_power_w = _as_float(solar.get("grid_power_w"), self.grid_power_w)
            if solar.get("battery_soc") is not None:
                self.battery_soc = _as_float(solar.get("battery_soc"), self.battery_soc or 0.0)

        payload = frame.get("connectors")
        if payload is None and "connector_id" in frame:
            payload = [frame]  # single-connector shorthand
        for entry in payload or []:
            if not isinstance(entry, dict):
                continue
            runtime = self.connector(int(entry.get("connector_id", 1)))
            runtime.voltage = _as_float(entry.get("voltage"), runtime.voltage)
            runtime.current = _as_float(entry.get("current"), runtime.current)
            runtime.power_w = _as_float(
                entry.get("power", entry.get("power_w")), runtime.power_w
            )
            runtime.energy_kwh = _as_float(
                entry.get("energy_kwh", entry.get("energy")), runtime.energy_kwh
            )
            runtime.frequency = _as_float(entry.get("frequency"), runtime.frequency)
            runtime.power_factor = _as_float(entry.get("power_factor"), runtime.power_factor)
            if entry.get("temperature_c") is not None:
                runtime.temperature_c = _as_float(entry.get("temperature_c"), 0.0)
            if entry.get("soc") is not None:
                runtime.soc = _as_float(entry.get("soc"), runtime.soc or 0.0)
            if entry.get("battery_capacity_kwh") is not None:
                runtime.battery_capacity_kwh = _as_float(
                    entry.get("battery_capacity_kwh"), 0.0
                )
            if entry.get("vehicle_connected") is not None:
                runtime.vehicle_connected = bool(entry["vehicle_connected"])
            if runtime.meter_start_kwh is None and runtime.session_active:
                runtime.meter_start_kwh = runtime.energy_kwh

    # ----------------------------------------------------------------- events
    def push_event(
        self,
        level: EventLevel,
        code: str,
        message: str,
        connector_id: int | None = None,
    ) -> dict:
        event = {
            "id": f"{self.tick}-{len(self.events)}-{code}",
            "at": utcnow().isoformat() + "Z",
            "level": level.value,
            "code": code,
            "message": message,
            "connector_id": connector_id,
        }
        self.events.append(event)
        return event

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """Single payload broadcast to every dashboard client."""
        quote = self.billing.quote(solar_share=self.solar_share)
        decision = self.decision.to_dict() if self.decision else {}
        active = [c for c in self.connectors.values() if c.session_active]
        return {
            "type": "state",
            "tick": self.tick,
            "server_time": utcnow().isoformat() + "Z",
            "local_time": self.billing.to_local().isoformat(),
            "station": {
                "station_id": self.station_id,
                "online": self.online,
                "firmware": self.firmware,
                "frames_received": self.frames_received,
                "last_frame_at": (
                    self.last_frame_at.isoformat() + "Z" if self.last_frame_at else None
                ),
            },
            "site": {
                "power_limit_w": round(self.power_limit_w, 1),
                "evse_power_w": round(self.total_power_w, 1),
                "house_load_w": round(self.house_load_w, 1),
                "measured_total_w": round(self.measured_total_w, 1),
                "pv_power_w": round(self.pv_power_w, 1),
                "grid_power_w": round(self.grid_power_w, 1),
                "battery_soc": (
                    round(self.battery_soc, 1) if self.battery_soc is not None else None
                ),
                "solar_share": round(self.solar_share, 4),
                "voltage": round(
                    next(
                        (c.voltage for c in self.connectors.values() if c.voltage > 90.0),
                        0.0,
                    ),
                    1,
                ),
                "total_current_a": round(sum(c.current for c in self.connectors.values()), 2),
                "session_kwh": round(sum(c.acc.total_kwh for c in active), 4),
                "session_cost": round(sum(c.running_cost for c in active), 3),
                "active_sessions": len(active),
                **decision,
            },
            "tariff": {
                **self.tariff.to_dict(),
                "window": quote.window.value,
                "is_peak": quote.window is TariffWindow.PEAK,
                "effective_rate": round(quote.rate, 4),
                "grid_rate": round(quote.grid_rate, 4),
                "solar_share": round(quote.solar_share, 4),
                "seconds_to_window_change": round(self.billing.seconds_until_window_change()),
            },
            "limits": {
                "min_current_a": self.min_current_a,
                "max_current_a": self.max_current_a,
                "eco_current_a": self.eco_current_a,
                "nominal_voltage": config.NOMINAL_VOLTAGE,
                "phases": config.PHASES,
                "solar_priority": self.solar_priority,
                "overload_latched": bool(self.decision and self.decision.overload),
            },
            "connectors": [c.to_dict() for c in sorted(self.connectors.values(), key=lambda x: x.connector_id)],
            "events": list(self.events)[-20:][::-1],
        }

    def control_frame(self) -> dict:
        """Command frame sent back to the ESP32 after every control tick."""
        return {
            "type": "control",
            "tick": self.tick,
            "station_id": self.station_id,
            "connectors": [
                {
                    "connector_id": c.connector_id,
                    "relay": c.relay_closed,
                    "setpoint_a": round(c.setpoint_a, 2),
                    "state": c.state.value,
                    "reason": c.reason,
                }
                for c in sorted(self.connectors.values(), key=lambda x: x.connector_id)
            ],
        }


def _as_float(value, fallback: float) -> float:
    try:
        if value is None:
            return fallback
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback  # drop NaN


# Process-wide singleton — the station is a physical thing, there is only one.
station = StationState()
