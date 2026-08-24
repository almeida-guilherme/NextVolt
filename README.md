# GoodWe Challenge — Smart EV Charging Station

A full-stack EV charging station manager: **Dynamic Load Balancing** across
connectors, **time-of-use billing** that is integrated second by second, and a
**real-time dashboard** fed by WebSockets. Runs against a PZEM-004T instrumented
ESP32, or against the bundled simulator when there is no hardware on the bench.

```
┌──────────────┐  telemetry 5 Hz   ┌───────────────────────┐   state 1 Hz   ┌──────────────┐
│ ESP32+PZEM   │ ────────────────► │  FastAPI              │ ─────────────► │  React       │
│ or simulator │ ◄──────────────── │  power_manager (DLB)  │ ◄───────────── │  dashboard   │
└──────────────┘  relay+setpoint   │  billing_service      │   REST commands└──────────────┘
                                   │  SQLite (SQLAlchemy)  │
                                   └───────────────────────┘
```

---

## Run it — two commands

```bash
# terminal 1 — API + telemetry simulator (creates the venv on first run)
./scripts/start-backend.sh

# terminal 2 — dashboard
./scripts/start-frontend.sh
```

Then open **http://localhost:5173**. API docs live at
**http://localhost:8000/docs**.

Want the whole scenario to play itself — two cars, DLB throttling, an overload
cutoff and two invoices — without touching the UI?

```bash
./scripts/start-backend.sh --demo --speed 60
```

`--speed 60` accelerates only the *energy counter*, so one real second bills as
one minute of charging. The instantaneous readings stay physically plausible.

### Verify the whole flow with assertions

```bash
backend/.venv/bin/python scripts/verify_e2e.py
```

Boots a throwaway API on a free port, attaches the simulator, and asserts all
30 checks: ingest → session → cost accrual → DLB fair share → DLB queueing →
overload cutoff → latch reset → invoice → history → tariff switch.

---

## Project layout

```
backend/
  app/
    main.py             FastAPI app: REST control plane + /ws/telemetry
    orchestrator.py     the 1 Hz control loop; every state mutation lives here
    power_manager.py    Dynamic Load Balancing + overload latch
    billing_service.py  time-of-use pricing, incremental cost accrual
    station_state.py    in-memory mirror of the physical station
    session_service.py  synchronous SQLAlchemy persistence helpers
    models.py           ChargingSession, TariffConfig, SystemConfig, samples, events
    database.py         SQLite engine (WAL), session factory
    ws_manager.py       WebSocket hub (station role vs dashboard role)
    schemas.py          Pydantic request/response contracts
    config.py           electrical constants + env overrides
frontend/               React 18 + Vite + Tailwind + Recharts + Lucide
simulator/
  mock_esp32.py         ESP32 + PZEM + EV battery + PV plant simulator
firmware_esp32/
  src/main.cpp          Arduino/ESP32 firmware (PZEM UART -> WebSocket)
  platformio.ini        build config; see firmware_esp32/README.md for wiring
scripts/                start-backend.sh · start-frontend.sh · verify_e2e.py
```

---

## 1. Smart power demand management

`backend/app/power_manager.py`. Every control tick:

1. **Mode → current request.** Each connector's mode becomes a current ceiling:

   | Mode | Request | Behaviour |
   |---|---|---|
   | `ECO` | `eco_current_a` (10 A default) | Gentle, cheapest amps |
   | `FAST` | `max_current_a` (32 A) | As much as the site allows |
   | `SOLAR` | PV surplus ÷ solar connectors | Rides `pv_power − house_load`; suspends below 6 A |
   | `OFF_PEAK` | `max_current_a`, but 0 during the peak window | Waits for the cheap window |

   An SOC taper sits on top (65 % of the cap above 80 % SOC, 35 % above 90 %,
   stop at 98 %), so a nearly-full car frees amps for its neighbour before the
   DLB has to take them.

2. **Real budget.** `budget = site_limit − building_load`. The building load is
   what the meter upstream of the chargers sees, so the DLB shares only what is
   genuinely spare — this is what a real load balancer does.

3. **Solar priority (optional, on by default).** PV surplus is reserved for
   `SOLAR`-mode connectors *before* the general share, so a `FAST` session
   cannot eat the free energy. With a 3.6 kW limit and 2.2 kW of surplus, the
   solar car keeps its 10 A and the fast car is queued; turn the flag off and
   they split 7.7 A each.

4. **Progressive water-filling.** Max-min fair share: small requests are served
   in full, the surplus is split evenly among the greedy ones. Setpoints are
   *floored*, never rounded up — the site limit is a breaker, not a target.

5. **The 6 A floor (IEC 61851).** A vehicle cannot be modulated below 6 A. If
   the budget cannot give every connector 6 A, the newest session is
   **suspended** rather than starved, and the FIFO holder keeps charging. It
   resumes automatically the moment headroom reappears.

6. **Hard cutoff.** If measured site load stays above `limit × 1.05` for 3
   consecutive ticks, every relay opens and the latch holds. It re-arms once the
   load sits under `limit × 0.90` for the cooldown, or immediately via
   `POST /api/system/reset-overload`.

A 0.5 A deadband on the throttle state keeps a wobbling house load from
flapping the UI.

## 2. Billing and dynamic pricing

`backend/app/billing_service.py`. Cost is **integrated, never multiplied at the
end**:

```
delta_kwh  = meter_counter_delta  (or power_w × dt / 3_600_000 without a meter)
delta_cost = delta_kwh × rate_at(now)
```

That is what makes a session running from 17:50 to 18:30 correct: the kWh burnt
before the peak window opened stay cheap forever. Each session therefore stores
its energy split across `peak_kwh` / `off_peak_kwh` / `solar_kwh`.

- **Time-of-use.** Peak and off-peak rates with a configurable window that may
  wrap midnight (`22:00 → 06:00` works). Windows are evaluated in **local**
  time (`GOODWE_TIMEZONE`, default `America/Sao_Paulo`) while every timestamp is
  stored in UTC.
- **Solar blending.** The share of the load covered by on-site PV is billed at
  the self-consumption rate: `rate = share × solar_rate + (1 − share) × grid_rate`.
  With 29 % PV coverage off-peak, the dashboard shows R$ 0.49/kWh instead of
  R$ 0.62.
- **Invoice.** `total = energy_cost + service_fee`, with payment status
  (`PENDING` / `PAID` / `REFUNDED`), duration, average and peak power, and start
  and end SOC.
- **Crash safety.** Progress is checkpointed every 5 s; a session left open by
  an unclean shutdown is aborted at boot rather than silently billed.

## 3. Real-time user interface

`frontend/`. One WebSocket (`/ws/telemetry?role=dashboard`) carries a single
aggregated snapshot at the control-loop rate; commands go over REST.

- Live telemetry: **Voltage · Current · Active power · Session energy · Solar
  coverage · Running cost** (the hero figure).
- Two-part connection status — the browser link and the station's own heartbeat
  fail independently, and the operator needs to know which one broke.
- Per-connector card: EV battery SOC meter, live meter readings, allocated
  versus actual current, the four mode buttons, start/stop.
- Site power panel: load meter with severity, EVSE / building / PV / headroom
  split, the DLB verdict, and the site-limit control.
- Charts: stacked connector power against the site-limit reference line with PV
  overlaid, plus running cost on **its own chart** (power and money never share
  an axis). Both have a crosshair tooltip; the power chart has a table view.
- Tariff editor, session history with invoices and "mark paid", and a live feed
  of every load-balancing decision.

The dark palette is a *selected* set of steps for the `#1a1a19` surface, checked
against the lightness band, chroma floor, CVD separation (worst ΔE 9.4),
normal-vision floor (worst ΔE 20.9) and 3:1 contrast. Series colours follow the
connector, never its rank, and identity is always carried by a legend or a
labelled dot — never colour alone.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness, connected peers, tick counter |
| `GET` | `/api/state` | The same snapshot the WebSocket pushes |
| `POST` | `/api/sessions/start` | Open a session (`connector_id`, `mode`, `driver_label`) |
| `POST` | `/api/sessions/stop` | Close a session and return the invoice |
| `GET` | `/api/sessions/history` | Closed sessions + aggregate revenue |
| `GET` | `/api/sessions/active` | Sessions currently running |
| `GET` | `/api/sessions/{id}` | One session with its telemetry samples |
| `POST` | `/api/sessions/{id}/payment` | Mark paid / refunded |
| `POST` | `/api/config/power-limit` | Update the DLB envelope |
| `POST` | `/api/config/tariff` | Update rates, peak window, session fee |
| `GET` | `/api/config` | Current limits + tariff |
| `POST` | `/api/system/reset-overload` | Clear the overload latch |
| `GET` | `/api/telemetry/history` | Down-sampled samples for the charts |
| `POST` | `/api/telemetry` | HTTP ingest fallback for constrained firmware |
| `GET` | `/api/events` | Audit trail of control decisions |
| `WS` | `/ws/telemetry?role=station` | Meter frames up, relay + setpoint down |
| `WS` | `/ws/telemetry?role=dashboard` | Aggregated state snapshots |

### WebSocket frames

```jsonc
// station -> server
{"type":"telemetry","station_id":"GW-EVSE-01",
 "connectors":[{"connector_id":1,"voltage":220.4,"current":15.9,"power":3504,
                "energy_kwh":41.2,"soc":63.5,"vehicle_connected":true}],
 "solar":{"pv_power_w":2600,"house_load_w":540,"battery_soc":68}}

// server -> station (1 Hz)
{"type":"control","tick":842,
 "connectors":[{"connector_id":1,"relay":true,"setpoint_a":16.11,
                "state":"THROTTLED","reason":"DLB_SHARED_LIMIT"}]}
```

---

## Configuration

Every constant in `backend/app/config.py` has an environment override:

| Variable | Default | Meaning |
|---|---|---|
| `GOODWE_POWER_LIMIT_W` | `7400` | Site limit used by the DLB |
| `GOODWE_CONNECTOR_COUNT` | `2` | Number of charge points |
| `GOODWE_NOMINAL_VOLTAGE` / `GOODWE_PHASES` | `220` / `1` | Electrical envelope |
| `GOODWE_MIN_CHARGE_CURRENT_A` | `6` | IEC 61851 modulation floor |
| `GOODWE_MAX_CHARGE_CURRENT_A` | `32` | Per-connector ceiling |
| `GOODWE_OVERLOAD_FACTOR` / `_GRACE_TICKS` | `1.05` / `3` | Cutoff trip point |
| `GOODWE_PEAK_RATE` / `GOODWE_OFF_PEAK_RATE` | `1.35` / `0.62` | Seed tariff (BRL/kWh) |
| `GOODWE_PEAK_START` / `GOODWE_PEAK_END` | `18:00` / `21:00` | Peak window (local) |
| `GOODWE_TIMEZONE` | `America/Sao_Paulo` | Clock used for time-of-use |
| `GOODWE_DATABASE_URL` | `sqlite:///backend/goodwe_station.db` | Persistence |

The tariff and the power limit are also persisted in SQLite once changed from
the UI, so they survive a restart.

## Single-process deployment

```bash
cd frontend && npm run build      # emits frontend/dist
cd ../backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

FastAPI mounts `frontend/dist` at `/` when it exists, so the dashboard, the API
and the WebSocket are served from one origin — no CORS, no proxy.

## Simulator options

```
--connectors N     charge points to simulate      (default 2)
--interval S       seconds between frames         (default 0.2 → 5 Hz)
--speed X          energy/SOC acceleration        (default 1)
--capacity KWH     EV battery capacity            (default 60)
--soc PCT          initial SOC of connector 1     (default 22)
--pv W             force a fixed PV output        (default: bell curve by hour)
--pv-peak W        PV array peak power            (default 5200)
--house-load W     baseline building load         (default 550)
--demo             run the scripted scenario
--verbose          print a telemetry summary
```

## Notes and limitations

- Payment is a status field, not a real gateway integration — the challenge asks
  for session records with a payment status, and that is what is modelled.
- The DLB priority rule is FIFO. Swapping in a fairness or reservation policy
  means changing one sort key in `PowerManager.evaluate`.
- Telemetry samples are down-sampled to 5 s before being persisted;
  `purge_old_samples` exists for retention but is not scheduled.
- Node 16 is the floor here, so the frontend is pinned to Vite 4 / Tailwind 3.
