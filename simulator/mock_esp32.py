#!/usr/bin/env python3
"""Mock ESP32 + PZEM-004T + EV battery — no hardware required.

Speaks exactly the same WebSocket protocol as `firmware_esp32/src/main.cpp`:

    -> {"type":"telemetry", "connectors":[{...PZEM readings...}], "solar":{...}}
    <- {"type":"control",   "connectors":[{"relay":true,"setpoint_a":16.0}]}

so the backend, the dashboard and the DLB engine cannot tell the difference.

Examples
--------
    # plain stream, 2 connectors, 5 Hz
    python simulator/mock_esp32.py

    # full self-driving demo: opens sessions, forces DLB throttling,
    # then trips the overload cutoff. 60x accelerated energy.
    python simulator/mock_esp32.py --demo --speed 60
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import random
import signal
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: pip install websockets  (or use backend/.venv)")

NOMINAL_VOLTAGE = 220.0
CHARGE_EFFICIENCY = 0.92
CURRENT_SLEW_A_PER_S = 8.0     # how fast the EV follows a new setpoint
JOULES_PER_KWH = 3_600_000.0


# --------------------------------------------------------------------- models
@dataclass
class Vehicle:
    """Simple EV battery: SOC integrates delivered energy, tapers when full."""

    capacity_kwh: float = 60.0
    soc: float = 22.0
    connected: bool = True

    def absorb(self, energy_kwh: float) -> None:
        if self.capacity_kwh <= 0:
            return
        self.soc = min(100.0, self.soc + (energy_kwh * CHARGE_EFFICIENCY / self.capacity_kwh) * 100.0)

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


@dataclass
class Connector:
    connector_id: int
    vehicle: Vehicle = field(default_factory=Vehicle)
    setpoint_a: float = 0.0
    relay_closed: bool = False
    current_a: float = 0.0
    energy_kwh: float = 0.0        # cumulative PZEM counter (survives sessions)

    def step(self, dt: float, voltage: float, speed: float) -> tuple[float, float]:
        """Advance one physics step. Returns (current_a, power_w)."""
        target = self.setpoint_a * self.vehicle.acceptance if (
            self.relay_closed and self.vehicle.connected
        ) else 0.0

        # first-order slew so the dashboard shows a realistic ramp
        delta = target - self.current_a
        step = min(abs(delta), CURRENT_SLEW_A_PER_S * dt)
        self.current_a += math.copysign(step, delta)
        if self.current_a < 0.2:
            self.current_a = 0.0

        power = self.current_a * voltage
        # `speed` accelerates *energy* only: the backend prefers the cumulative
        # kWh counter over integrating power, so demos bill fast while the
        # instantaneous readings stay physically plausible.
        energy = power * dt * speed / JOULES_PER_KWH
        self.energy_kwh += energy
        self.vehicle.absorb(energy)
        return self.current_a, power


class SolarPlant:
    """GoodWe hybrid inverter feed: PV bell curve + house load random walk."""

    def __init__(self, pv_peak_w: float, house_base_w: float, pv_override: float | None) -> None:
        self.pv_peak_w = pv_peak_w
        self.house_base_w = house_base_w
        self.pv_override = pv_override
        self.house_load_w = house_base_w
        self.extra_load_w = 0.0
        self.battery_soc = 68.0

    def pv_power(self, now: datetime) -> float:
        if self.pv_override is not None:
            return max(self.pv_override, 0.0)
        hour = now.hour + now.minute / 60.0
        if not 5.5 <= hour <= 18.5:
            return 0.0
        arc = math.sin(math.pi * (hour - 5.5) / 13.0)
        return max(0.0, self.pv_peak_w * (arc ** 1.6) * random.uniform(0.93, 1.03))

    def step(self, dt: float) -> None:
        drift = random.uniform(-140.0, 140.0) * dt
        self.house_load_w = min(max(self.house_load_w + drift, 150.0), self.house_base_w * 2.6)
        self.battery_soc = min(100.0, max(5.0, self.battery_soc + random.uniform(-0.02, 0.02)))

    @property
    def total_house_w(self) -> float:
        return self.house_load_w + self.extra_load_w


# ------------------------------------------------------------------ simulator
class MockStation:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.connectors = {
            cid: Connector(
                connector_id=cid,
                vehicle=Vehicle(capacity_kwh=args.capacity, soc=args.soc + (cid - 1) * 7.0),
            )
            for cid in range(1, args.connectors + 1)
        }
        self.solar = SolarPlant(args.pv_peak, args.house_load, args.pv)
        self.voltage = NOMINAL_VOLTAGE
        self.running = True
        self.elapsed = 0.0
        self.frames = 0

    # ---------------------------------------------------------------- physics
    def build_frame(self) -> dict:
        dt = self.args.interval
        self.elapsed += dt
        self.solar.step(dt)

        readings = []
        total_current = 0.0
        for connector in self.connectors.values():
            current, power = connector.step(dt, self.voltage, self.args.speed)
            total_current += current
            readings.append(
                {
                    "connector_id": connector.connector_id,
                    "voltage": round(self.voltage + random.uniform(-0.4, 0.4), 2),
                    "current": round(current + random.uniform(-0.03, 0.03) if current else 0.0, 3),
                    "power": round(power, 1),
                    "energy_kwh": round(connector.energy_kwh, 5),
                    "frequency": round(60.0 + random.uniform(-0.04, 0.04), 2),
                    "power_factor": round(0.985 + random.uniform(-0.01, 0.012), 3) if current else 0.0,
                    "temperature_c": round(28.0 + current * 0.55 + random.uniform(-0.6, 0.6), 1),
                    "vehicle_connected": connector.vehicle.connected,
                    "soc": round(connector.vehicle.soc, 2),
                    "battery_capacity_kwh": connector.vehicle.capacity_kwh,
                    "relay_closed": connector.relay_closed,
                }
            )

        # Grid voltage sags a little under load — makes the dashboard look alive.
        self.voltage = NOMINAL_VOLTAGE - 0.12 * total_current + random.uniform(-0.5, 0.5)
        self.frames += 1

        pv = self.solar.pv_power(datetime.now())
        evse = sum(r["power"] for r in readings)
        return {
            "type": "telemetry",
            "station_id": self.args.station_id,
            "firmware": "mock-esp32/1.0.0",
            "seq": self.frames,
            "connectors": readings,
            "solar": {
                "pv_power_w": round(pv, 1),
                "house_load_w": round(self.solar.total_house_w, 1),
                "grid_power_w": round(evse + self.solar.total_house_w - pv, 1),
                "battery_soc": round(self.solar.battery_soc, 1),
            },
        }

    def apply_control(self, frame: dict) -> None:
        for command in frame.get("connectors", []):
            connector = self.connectors.get(int(command.get("connector_id", 0)))
            if connector is None:
                continue
            connector.setpoint_a = float(command.get("setpoint_a", 0.0) or 0.0)
            connector.relay_closed = bool(command.get("relay", False))

    # ------------------------------------------------------------------- demo
    def api(self, path: str, payload: dict | None = None) -> dict | None:
        """Tiny stdlib REST client so the demo can drive the control plane."""
        url = self.args.api.rstrip("/") + path
        data = json.dumps(payload or {}).encode()
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            print(f"  ! {path} -> HTTP {exc.code}: {exc.read().decode()[:160]}")
        except OSError as exc:
            print(f"  ! {path} -> {exc}")
        return None

    async def demo_script(self) -> None:
        """Scripted scenario that exercises every evaluation criterion."""
        steps = [
            (2.0, "Connector 1 starts charging in FAST mode", lambda: self.api(
                "/api/sessions/start", {"connector_id": 1, "mode": "FAST", "driver_label": "Ana"})),
            (14.0, "Connector 2 joins in FAST mode -> DLB must split the 7.4 kW",
             lambda: self.api("/api/sessions/start",
                              {"connector_id": 2, "mode": "FAST", "driver_label": "Bruno"})),
            (16.0, "Operator drops the site limit to 3.6 kW -> connector 2 gets queued",
             lambda: self.api("/api/config/power-limit", {"power_limit_w": 3600})),
            (16.0, "Connector 1 switches to ECO", lambda: self.api(
                "/api/connectors/mode", {"connector_id": 1, "mode": "ECO"})),
            (10.0, "House load spike (+6 kW) -> overload cutoff must latch",
             lambda: setattr(self.solar, "extra_load_w", 6000.0)),
            (18.0, "Spike cleared -> latch auto-rearms after the cooldown",
             lambda: setattr(self.solar, "extra_load_w", 0.0)),
            (14.0, "Site limit restored to 7.4 kW", lambda: self.api(
                "/api/config/power-limit", {"power_limit_w": 7400})),
            (20.0, "Connector 1 stops and gets its invoice", lambda: self.api(
                "/api/sessions/stop", {"connector_id": 1, "reason": "DEMO_STOP", "mark_paid": True})),
            (8.0, "Connector 2 stops", lambda: self.api(
                "/api/sessions/stop", {"connector_id": 2, "reason": "DEMO_STOP"})),
        ]
        print("\n=== DEMO SCRIPT ===")
        for delay, label, action in steps:
            await asyncio.sleep(delay)
            if not self.running:
                return
            print(f"[t+{self.elapsed:6.1f}s] {label}")
            result = action()
            if isinstance(result, dict) and "invoice" in result:
                invoice = result["invoice"]
                print(
                    f"           invoice: {invoice['total_kwh']:.3f} kWh -> "
                    f"{invoice['currency']} {invoice['total_cost']:.2f} "
                    f"(energy {invoice['energy_cost']:.2f} + fee {invoice['service_fee']:.2f})"
                )
        print("=== DEMO COMPLETE — dashboard history now has two paid sessions ===\n")

    # -------------------------------------------------------------- transport
    async def run(self) -> None:
        url = f"{self.args.url}?role=station"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20) as socket:
                    print(f"[mock-esp32] connected to {url}")
                    tasks = [
                        asyncio.create_task(self._pump(socket)),
                        asyncio.create_task(self._listen(socket)),
                    ]
                    if self.args.demo:
                        tasks.append(asyncio.create_task(self.demo_script()))
                    try:
                        await asyncio.gather(*tasks)
                    finally:
                        for task in tasks:
                            task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self.running:
                    return
                print(f"[mock-esp32] link down ({exc.__class__.__name__}: {exc}); retrying in 2 s")
                await asyncio.sleep(2.0)

    async def _pump(self, socket) -> None:
        next_log = 0.0
        while self.running:
            frame = self.build_frame()
            await socket.send(json.dumps(frame))
            if self.args.verbose and self.elapsed >= next_log:
                next_log = self.elapsed + 2.0
                summary = " | ".join(
                    f"C{r['connector_id']} {r['current']:5.2f}A {r['power']:7.1f}W "
                    f"SOC {r['soc']:5.1f}%"
                    for r in frame["connectors"]
                )
                print(
                    f"[t+{self.elapsed:6.1f}s] {summary} | PV {frame['solar']['pv_power_w']:7.1f}W "
                    f"| house {frame['solar']['house_load_w']:7.1f}W"
                )
            await asyncio.sleep(self.args.interval)

    async def _listen(self, socket) -> None:
        async for raw in socket:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "control":
                self.apply_control(message)
            elif message.get("type") == "welcome":
                print(
                    f"[mock-esp32] server hello: station={message.get('station_id')} "
                    f"connectors={message.get('connector_ids')}"
                )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GoodWe challenge — ESP32/PZEM-004T telemetry simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/telemetry", help="WebSocket URL")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="REST base URL (demo mode)")
    parser.add_argument("--station-id", default="GW-EVSE-01")
    parser.add_argument("--connectors", type=int, default=2, help="number of connectors")
    parser.add_argument("--interval", type=float, default=0.2, help="seconds between frames")
    parser.add_argument("--capacity", type=float, default=60.0, help="EV battery capacity (kWh)")
    parser.add_argument("--soc", type=float, default=22.0, help="initial SOC of connector 1 (%)")
    parser.add_argument("--house-load", type=float, default=550.0, help="baseline house load (W)")
    parser.add_argument("--pv-peak", type=float, default=5200.0, help="PV array peak power (W)")
    parser.add_argument("--pv", type=float, default=None, help="force a fixed PV output (W)")
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="energy/SOC time acceleration (60 = one minute of charging per second)",
    )
    parser.add_argument("--demo", action="store_true", help="run the scripted end-to-end scenario")
    parser.add_argument("--verbose", action="store_true", help="print a telemetry summary")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    station = MockStation(args)
    loop = asyncio.get_running_loop()

    def shutdown() -> None:
        station.running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown)

    print(
        f"[mock-esp32] {args.connectors} connector(s), {1 / args.interval:.1f} Hz, "
        f"speed x{args.speed:g}"
    )
    await station.run()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass
    print("[mock-esp32] stopped")


if __name__ == "__main__":
    main()
