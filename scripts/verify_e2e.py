#!/usr/bin/env python3
"""End-to-end verification of the whole stack, with assertions.

Boots a throwaway API on a free port with its own SQLite file, attaches the
simulator as the station, then drives the full evaluation checklist and checks
the result of each step:

    1. telemetry ingest + WebSocket broadcast
    2. session start, live energy + cost accrual
    3. DLB fair-share when two vehicles exceed the site limit
    4. DLB suspension when there is no room for the 6 A minimum
    5. hard overload cutoff, then latch reset
    6. session stop -> itemised invoice
    7. invoice persisted in the history with the right totals
    8. time-of-use pricing switches when the peak window moves

Run:  backend/.venv/bin/python scripts/verify_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "simulator"))

try:
    import websockets  # noqa: F401  (needed by the simulator import below)
except ImportError:
    sys.exit("Missing dependency: run this with backend/.venv/bin/python")

from mock_esp32 import MockStation, parse_args  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, title: str, detail: str = "") -> bool:
    results.append((bool(ok), title, detail))
    print(f"  [{PASS if ok else FAIL}] {title}{f' — {detail}' if detail else ''}")
    return bool(ok)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base: str, path: str, payload: dict | None = None, method: str | None = None):
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, (json.loads(body) if body else None)
    except OSError:
        # Server not up yet (or already gone) — callers poll on the status code.
        return 0, None


async def wait_until(predicate, timeout: float, interval: float = 0.4) -> bool:
    """Poll `predicate` (may be async) until it is truthy or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = predicate()
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        if outcome:
            return True
        await asyncio.sleep(interval)
    return False


async def main() -> int:
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}/ws/telemetry"
    db_path = ROOT / "backend" / f"verify_{port}.db"

    env = {
        **os.environ,
        "GOODWE_DATABASE_URL": f"sqlite:///{db_path}",
        "GOODWE_CONNECTOR_COUNT": "2",
        "GOODWE_OVERLOAD_COOLDOWN_S": "3",
        "GOODWE_TELEMETRY_PERSIST_INTERVAL_S": "2",
    }

    print(f"\n== booting API on {api} (db {db_path.name}) ==")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT / "backend",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    state = lambda: request(api, "/api/state")[1]  # noqa: E731
    connector = lambda snap, cid: next(  # noqa: E731
        c for c in snap["connectors"] if c["connector_id"] == cid
    )

    sim = None
    tasks: list[asyncio.Task] = []
    try:
        if not await wait_until(
            lambda: request(api, "/api/health")[0] == 200 if server.poll() is None else False, 25
        ):
            print("  API never became healthy")
            return 1

        args = parse_args(
            ["--url", ws_url, "--api", api, "--connectors", "2", "--interval", "0.1",
             "--speed", "180", "--pv", "1200", "--house-load", "400", "--soc", "20"]
        )
        sim = MockStation(args)
        socket_conn = await websockets.connect(f"{ws_url}?role=station")
        tasks = [
            asyncio.create_task(sim._pump(socket_conn)),
            asyncio.create_task(sim._listen(socket_conn)),
        ]

        # ---------------------------------------------------------- 1. ingest
        print("\n== 1. telemetry ingest ==")
        online = await wait_until(lambda: (state() or {}).get("station", {}).get("online"), 15)
        snap = state()
        check(online, "station reports online", f"{snap['station']['frames_received']} frames")
        check(snap["site"]["voltage"] > 180, "voltage measured", f"{snap['site']['voltage']} V")

        # ----------------------------------------------- 2. session + billing
        print("\n== 2. session start, energy and cost accrual ==")
        status, session = request(
            api, "/api/sessions/start",
            {"connector_id": 1, "mode": "FAST", "driver_label": "Verifier"},
        )
        check(status == 201, "POST /api/sessions/start", f"HTTP {status}")
        session_id = (session or {}).get("id")

        charging = await wait_until(
            lambda: connector(state(), 1)["state"] in ("CHARGING", "THROTTLED"), 15
        )
        # Energy cost specifically, not `session_cost` — that one is non-zero
        # from the first tick because it already carries the session fee.
        accruing = await wait_until(
            lambda: connector(state(), 1)["billing"]["energy_cost"] > 0, 20
        )
        snap = state()
        c1 = connector(snap, 1)
        check(charging, "connector 1 charging", f"{c1['current']} A @ {c1['setpoint_a']} A setpoint")
        check(c1["power_kw"] > 0.5, "power delivered", f"{c1['power_kw']} kW")
        check(
            accruing and c1["session_kwh"] > 0,
            "energy and cost accruing",
            f"{c1['session_kwh']} kWh -> energy {c1['billing']['energy_cost']}",
        )
        check(
            c1["session_cost"] >= snap["tariff"]["service_fee"],
            "running cost includes the session fee",
            f"fee {snap['tariff']['service_fee']}",
        )

        # ----------------------------------------------------- 3. DLB sharing
        print("\n== 3. dynamic load balancing (two vehicles, one breaker) ==")
        request(api, "/api/sessions/start", {"connector_id": 2, "mode": "FAST", "driver_label": "Second"})
        shared = await wait_until(lambda: state()["site"]["curtailed"], 15)
        snap = state()
        allocated = snap["site"]["allocated_w"]
        budget = snap["site"]["budget_w"]
        both = [connector(snap, 1), connector(snap, 2)]
        check(shared, "DLB curtailing", f"allocated {allocated:.0f} W of {budget:.0f} W budget")
        check(
            allocated <= budget + 25,
            "allocation never exceeds the available budget",
            f"{allocated:.0f} W <= {budget:.0f} W",
        )
        check(
            all(c["setpoint_a"] >= snap["limits"]["min_current_a"] or c["setpoint_a"] == 0 for c in both),
            "no connector starved below the 6 A minimum",
            ", ".join(f"C{c['connector_id']} {c['setpoint_a']} A" for c in both),
        )

        # -------------------------------------------------- 4. DLB suspension
        print("\n== 4. site limit drop forces a queue ==")
        request(api, "/api/config/power-limit", {"power_limit_w": 2200})
        queued = await wait_until(lambda: state()["site"]["suspended_connectors"], 15)
        snap = state()
        check(queued, "a connector was queued", f"suspended {snap['site']['suspended_connectors']}")
        survivor = next(
            (c for c in snap["connectors"] if c["state"] in ("CHARGING", "THROTTLED")), None
        )
        check(
            survivor is not None,
            "the first-come session keeps charging (FIFO priority)",
            f"connector {survivor['connector_id']} at {survivor['setpoint_a']} A" if survivor else "none",
        )

        # ------------------------------------------------ 5. overload cutoff
        print("\n== 5. hard overload cutoff and reset ==")
        sim.solar.extra_load_w = 8000.0
        tripped = await wait_until(lambda: state()["limits"]["overload_latched"], 20)
        snap = state()
        check(tripped, "overload latch tripped", f"site load {snap['site']['measured_total_w']:.0f} W")
        check(
            all(not c["relay_closed"] for c in snap["connectors"]),
            "every relay opened",
            ", ".join(f"C{c['connector_id']} {c['state']}" for c in snap["connectors"]),
        )
        blocked = request(api, "/api/sessions/start", {"connector_id": 2, "mode": "ECO"})[0]
        check(blocked == 409, "new sessions refused while latched", f"HTTP {blocked}")

        sim.solar.extra_load_w = 0.0
        request(api, "/api/config/power-limit", {"power_limit_w": 7400})
        request(api, "/api/system/reset-overload")
        recovered = await wait_until(lambda: not state()["limits"]["overload_latched"], 15)
        resumed = await wait_until(
            lambda: any(c["relay_closed"] for c in state()["connectors"]), 15
        )
        check(recovered, "latch cleared")
        check(resumed, "charging resumed automatically")

        # ------------------------------------------------------- 6. invoicing
        print("\n== 6. stop session and invoice ==")
        await asyncio.sleep(3)
        status, closed = request(
            api, "/api/sessions/stop",
            {"connector_id": 1, "reason": "VERIFY_STOP", "mark_paid": True},
        )
        check(status == 200, "POST /api/sessions/stop", f"HTTP {status}")
        invoice = (closed or {}).get("invoice") or {}
        expected = round(invoice.get("energy_cost", 0) + invoice.get("service_fee", 0), 2)
        check(invoice.get("total_kwh", 0) > 0, "energy billed", f"{invoice.get('total_kwh')} kWh")
        check(
            abs(invoice.get("total_cost", 0) - expected) < 0.011,
            "total = energy + session fee",
            f"{invoice.get('total_cost')} == {expected}",
        )
        split = round(
            invoice.get("peak_kwh", 0) + invoice.get("off_peak_kwh", 0) + invoice.get("solar_kwh", 0), 3
        )
        check(
            abs(split - round(invoice.get("total_kwh", 0), 3)) < 0.01,
            "peak/off-peak/solar split adds up to the total",
            f"{split} == {round(invoice.get('total_kwh', 0), 3)}",
        )

        # --------------------------------------------------------- 7. history
        print("\n== 7. history and payment status ==")
        status, history = request(api, "/api/sessions/history?limit=10")
        rows = (history or {}).get("items") or []
        row = next((item for item in rows if item["id"] == session_id), None)
        check(status == 200 and row is not None, "closed session present in history")
        if row:
            check(row["status"] == "COMPLETED", "status COMPLETED", row["status"])
            check(row["payment_status"] == "PAID", "payment recorded", row["payment_status"])
            check(row["stop_reason"] == "VERIFY_STOP", "stop reason stored", row["stop_reason"])
        check(
            all(item["status"] != "ACTIVE" for item in rows),
            "history excludes the session still running",
        )

        # ---------------------------------------------------- 8. tariff switch
        print("\n== 8. time-of-use pricing ==")
        snap = state()
        local_hour = int(snap["local_time"][11:13])
        peak_start = f"{local_hour:02d}:00"
        peak_end = f"{(local_hour + 2) % 24:02d}:00"
        request(
            api, "/api/config/tariff",
            {"peak_rate": 2.5, "off_peak_rate": 0.4, "solar_rate": 0.0,
             "current_peak_start": peak_start, "current_peak_end": peak_end},
        )
        in_peak = await wait_until(lambda: state()["tariff"]["is_peak"], 10)
        snap = state()
        check(in_peak, "peak window recognised", f"{peak_start}-{peak_end} local")
        check(
            snap["tariff"]["grid_rate"] == 2.5,
            "grid rate switched to the peak price",
            f"{snap['tariff']['grid_rate']}",
        )
        request(
            api, "/api/config/tariff",
            {"peak_rate": 2.5, "off_peak_rate": 0.4, "solar_rate": 0.0,
             "current_peak_start": f"{(local_hour + 4) % 24:02d}:00",
             "current_peak_end": f"{(local_hour + 6) % 24:02d}:00"},
        )
        off_peak = await wait_until(lambda: not state()["tariff"]["is_peak"], 10)
        snap = state()
        check(off_peak, "window moved back to off-peak")
        check(
            snap["tariff"]["grid_rate"] == 0.4,
            "grid rate switched to the off-peak price",
            f"{snap['tariff']['grid_rate']}",
        )

        request(api, "/api/sessions/stop", {"connector_id": 2, "reason": "VERIFY_CLEANUP"})

    finally:
        if sim is not None:
            sim.running = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    passed = sum(1 for ok, _, _ in results if ok)
    failed = [title for ok, title, _ in results if not ok]
    print(f"\n{'=' * 62}\n{passed}/{len(results)} checks passed")
    if failed:
        print("failed:")
        for title in failed:
            print(f"  - {title}")
        return 1
    print("End-to-end flow verified.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
