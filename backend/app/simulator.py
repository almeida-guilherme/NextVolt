"""In-process stand-in for the ESP32, for deployments with no hardware.

`simulator/mock_esp32.py` is the real article — it exercises the WebSocket
protocol end to end and is what you run on a bench. But a hosted demo has
nobody on the other end of `/ws/telemetry?role=station`, and the station stays
offline forever: `_check_liveness` zeroes every meter, `_accrue_billing`
returns early, and the dashboard shows a started session drawing 0.00 kW.

This module closes that gap by producing the same frames locally and handing
them to `station.ingest()`. It reads back the setpoints the control loop wrote
onto the runtime, so the DLB, the SOC taper and the overload latch all behave
exactly as they do against hardware. In `auto` mode it steps aside the moment a
real station connects, so it can be left enabled in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
from datetime import datetime

from . import config
from .station_state import StationState, station
from .ws_manager import Role, manager

logger = logging.getLogger("goodwe.simulator")

CHARGE_EFFICIENCY = 0.92
CURRENT_SLEW_A_PER_S = 8.0  # how fast the EV follows a new setpoint
JOULES_PER_KWH = 3_600_000.0

# A finished car is unplugged and the next driver arrives with this much charge,
# otherwise the demo can only ever be run once before every pack sits at 100%.
NEXT_DRIVER_SOC = (18.0, 35.0)


class _Vehicle:
    """Simple EV battery: SOC integrates delivered energy, tapers when full."""

    def __init__(self, capacity_kwh: float, soc: float) -> None:
        self.capacity_kwh = capacity_kwh
        self.soc = soc

    def absorb(self, energy_kwh: float) -> None:
        if self.capacity_kwh <= 0:
            return
        gained = energy_kwh * CHARGE_EFFICIENCY / self.capacity_kwh * 100.0
        self.soc = min(100.0, self.soc + gained)

    @property
    def acceptance(self) -> float:
        """Fraction of the offered current the pack will actually take."""
        if self.soc >= 100.0:
            return 0.0
        if self.soc >= 95.0:
            return 0.25
        if self.soc >= 85.0:
            return 0.6
        return 1.0


class _Connector:
    def __init__(self, connector_id: int, vehicle: _Vehicle) -> None:
        self.connector_id = connector_id
        self.vehicle = vehicle
        self.current_a = 0.0
        self.energy_kwh = 0.0  # cumulative PZEM counter, survives sessions
        self.last_session_id: int | None = None

    def step(self, dt: float, voltage: float, setpoint_a: float, relay_closed: bool,
             speed: float) -> tuple[float, float]:
        """Advance one physics step. Returns (current_a, power_w)."""
        target = setpoint_a * self.vehicle.acceptance if relay_closed else 0.0

        # First-order slew so the dashboard shows a realistic ramp.
        delta = target - self.current_a
        self.current_a += math.copysign(min(abs(delta), CURRENT_SLEW_A_PER_S * dt), delta)
        if self.current_a < 0.2:
            self.current_a = 0.0

        power = self.current_a * voltage
        # `speed` accelerates energy only: the backend prefers the cumulative
        # kWh counter over integrating power, so demos bill fast while the
        # instantaneous readings stay physically plausible.
        energy = power * dt * speed / JOULES_PER_KWH
        self.energy_kwh += energy
        self.vehicle.absorb(energy)
        return self.current_a, power


class StationSimulator:
    """Feeds synthetic PZEM-004T frames into a `StationState`."""

    def __init__(self, state: StationState | None = None) -> None:
        self.station = state or station
        self.voltage = config.NOMINAL_VOLTAGE
        self.house_load_w = config.SIMULATOR_HOUSE_LOAD_W
        self.battery_soc = 68.0
        self.frames = 0
        self._task: asyncio.Task | None = None
        self._cars = {
            connector_id: _Connector(
                connector_id,
                _Vehicle(
                    config.SIMULATOR_CAPACITY_KWH,
                    min(config.SIMULATOR_START_SOC + (connector_id - 1) * 7.0, 95.0),
                ),
            )
            for connector_id in sorted(self.station.connectors)
        }

    # ------------------------------------------------------------- lifecycle
    @property
    def enabled(self) -> bool:
        return config.SIMULATOR_MODE in {"auto", "on", "1", "true", "yes"}

    @property
    def should_emit(self) -> bool:
        """In `auto` mode real hardware always wins."""
        if config.SIMULATOR_MODE == "auto":
            return manager.count(Role.STATION) == 0
        return True

    def start(self) -> None:
        if not self.enabled:
            logger.info("built-in simulator disabled (GOODWE_SIMULATOR=%s)", config.SIMULATOR_MODE)
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="goodwe-simulator")
            logger.info(
                "built-in simulator running — mode=%s, %.1f Hz, speed x%g",
                config.SIMULATOR_MODE,
                1.0 / max(config.SIMULATOR_INTERVAL_S, 0.05),
                config.SIMULATOR_SPEED,
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        dt = max(config.SIMULATOR_INTERVAL_S, 0.05)
        while True:
            try:
                if self.should_emit:
                    self.station.ingest(self.build_frame(dt))
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - never kill the loop
                logger.exception("simulator step failed")
            await asyncio.sleep(dt)

    # ---------------------------------------------------------------- physics
    def build_frame(self, dt: float) -> dict:
        self.frames += 1
        self._drift_house(dt)

        readings = []
        total_current = 0.0
        for connector_id, car in self._cars.items():
            runtime = self.station.connectors.get(connector_id)
            setpoint_a = runtime.setpoint_a if runtime else 0.0
            relay_closed = bool(runtime and runtime.relay_closed)
            self._swap_vehicle_if_session_ended(car, runtime)

            current, power = car.step(
                dt, self.voltage, setpoint_a, relay_closed, config.SIMULATOR_SPEED
            )
            total_current += current
            readings.append(
                {
                    "connector_id": connector_id,
                    "voltage": round(self.voltage + random.uniform(-0.4, 0.4), 2),
                    "current": round(current + random.uniform(-0.03, 0.03), 3) if current else 0.0,
                    "power": round(power, 1),
                    "energy_kwh": round(car.energy_kwh, 5),
                    "frequency": round(60.0 + random.uniform(-0.04, 0.04), 2),
                    "power_factor": round(0.985 + random.uniform(-0.01, 0.012), 3) if current else 0.0,
                    "temperature_c": round(28.0 + current * 0.55 + random.uniform(-0.6, 0.6), 1),
                    # Always plugged in: the DLB suspends a connector with no
                    # vehicle, which would make the demo look broken.
                    "vehicle_connected": True,
                    "soc": round(car.vehicle.soc, 2),
                    "battery_capacity_kwh": car.vehicle.capacity_kwh,
                    "relay_closed": relay_closed,
                }
            )

        # Grid voltage sags a little under load — makes the dashboard look alive.
        self.voltage = config.NOMINAL_VOLTAGE - 0.12 * total_current + random.uniform(-0.5, 0.5)

        pv = self._pv_power()
        evse = sum(reading["power"] for reading in readings)
        return {
            "type": "telemetry",
            "station_id": self.station.station_id,
            "firmware": "builtin-simulator/1.0.0",
            "seq": self.frames,
            "connectors": readings,
            "solar": {
                "pv_power_w": round(pv, 1),
                "house_load_w": round(self.house_load_w, 1),
                "grid_power_w": round(evse + self.house_load_w - pv, 1),
                "battery_soc": round(self.battery_soc, 1),
            },
        }

    @staticmethod
    def _swap_vehicle_if_session_ended(car: _Connector, runtime) -> None:
        session_id = runtime.session_id if runtime else None
        if car.last_session_id is not None and session_id is None:
            car.vehicle.soc = random.uniform(*NEXT_DRIVER_SOC)
        car.last_session_id = session_id

    def _pv_power(self) -> float:
        """GoodWe hybrid inverter feed: a PV bell curve over the local day."""
        now = datetime.now(config.TIMEZONE)
        hour = now.hour + now.minute / 60.0
        if not 5.5 <= hour <= 18.5:
            return 0.0
        arc = math.sin(math.pi * (hour - 5.5) / 13.0)
        return max(0.0, config.SIMULATOR_PV_PEAK_W * (arc ** 1.6) * random.uniform(0.93, 1.03))

    def _drift_house(self, dt: float) -> None:
        base = config.SIMULATOR_HOUSE_LOAD_W
        drift = random.uniform(-140.0, 140.0) * dt
        self.house_load_w = min(max(self.house_load_w + drift, 150.0), base * 2.6)
        self.battery_soc = min(100.0, max(5.0, self.battery_soc + random.uniform(-0.02, 0.02)))


simulator = StationSimulator()
