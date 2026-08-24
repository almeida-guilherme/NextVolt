# ESP32 firmware — PZEM-004T charging station controller

The firmware is the *hands*; the backend is the *brain*. It streams meter
readings up and applies the current setpoint / relay command that comes back,
while keeping every safety decision local.

## Wiring

| ESP32 pin | Connects to | Notes |
|---|---|---|
| GPIO16 (RX2) | PZEM-004T **TX** | Modbus-RTU, 9600 8N1 |
| GPIO17 (TX2) | PZEM-004T **RX** | |
| GPIO26 | Relay / contactor driver | Active HIGH, through an opto-isolator |
| GPIO25 | J1772 Control Pilot PWM | 1 kHz, duty = amps / 0.6 |
| GPIO27 | Proximity switch | `INPUT_PULLUP`, LOW = cable plugged in |
| GPIO2 | Status LED | On while the contactor is closed |
| 5 V / GND | PZEM logic supply | The PZEM AC side is mains — isolate it |

> The PZEM-004T's current transformer clamps around the **live** conductor of
> the charging circuit. Everything on that side is mains voltage: do the wiring
> de-energised, and keep the ESP32 in an isolated enclosure.

## Safety model

The backend decides *policy* (load balancing, tariffs, sessions). The firmware
refuses to be the weak link:

- setpoints above `HARDWARE_MAX_CURRENT_A` (32 A) are **clamped**, not obeyed;
- the contactor opens if no `control` frame arrives for `CONTROL_TIMEOUT_MS`
  (8 s) — losing Wi-Fi must never leave a relay closed;
- a reading above `OVER_CURRENT_TRIP_A` (35 A) latches a **local** trip without
  waiting for the server;
- the relay also requires the proximity switch to report a plugged cable.

## Build & flash

```bash
cd firmware_esp32
pio run -t upload
pio device monitor
```

Set `WIFI_SSID`, `WIFI_PASSWORD` and `BACKEND_HOST` at the top of
`src/main.cpp` (or pass them as build flags — see `platformio.ini`).

## Protocol

Identical to `simulator/mock_esp32.py`, so the backend cannot tell them apart:

```jsonc
// ESP32 -> backend, 5 Hz
{"type":"telemetry","station_id":"GW-EVSE-01",
 "connectors":[{"connector_id":1,"voltage":220.4,"current":15.9,"power":3504.0,
                "energy_kwh":41.2,"frequency":60.0,"power_factor":0.99,
                "vehicle_connected":true,"relay_closed":true}],
 "solar":{"pv_power_w":0,"house_load_w":0,"grid_power_w":3504.0}}

// backend -> ESP32, 1 Hz (control-loop rate)
{"type":"control","tick":1234,
 "connectors":[{"connector_id":1,"relay":true,"setpoint_a":16.0,
                "state":"THROTTLED","reason":"DLB_SHARED_LIMIT"}]}
```

If holding a WebSocket open is inconvenient, `POST /api/telemetry` accepts the
same telemetry body and answers with the current control frame.

## No hardware yet?

Run `python simulator/mock_esp32.py` instead — it speaks this exact protocol,
including the EV battery model and the GoodWe inverter feed.
