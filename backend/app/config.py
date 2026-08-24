"""Static configuration and electrical constants for the charging station.

Everything here can be overridden with environment variables so the same image
runs against a bench simulator or a real PZEM-004T equipped station.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- Persistence -----------------------------------------------------------
DATABASE_URL = os.getenv("GOODWE_DATABASE_URL", f"sqlite:///{BASE_DIR / 'goodwe_station.db'}")

# --- Station identity ------------------------------------------------------
STATION_ID = os.getenv("GOODWE_STATION_ID", "GW-EVSE-01")
CONNECTOR_COUNT = _env_int("GOODWE_CONNECTOR_COUNT", 2)

# Time-of-use tariffs are a *local* clock concept, telemetry is stored in UTC.
TIMEZONE = ZoneInfo(os.getenv("GOODWE_TIMEZONE", "America/Sao_Paulo"))

# --- Electrical envelope ---------------------------------------------------
NOMINAL_VOLTAGE = _env_float("GOODWE_NOMINAL_VOLTAGE", 220.0)
PHASES = _env_int("GOODWE_PHASES", 1)

# IEC 61851: a vehicle cannot be modulated below 6 A, it must be suspended.
MIN_CHARGE_CURRENT_A = _env_float("GOODWE_MIN_CHARGE_CURRENT_A", 6.0)
MAX_CHARGE_CURRENT_A = _env_float("GOODWE_MAX_CHARGE_CURRENT_A", 32.0)

# Site level ceiling used by the Dynamic Load Balancer (W).
DEFAULT_POWER_LIMIT_W = _env_float("GOODWE_POWER_LIMIT_W", 7400.0)

# Above limit * factor for `grace` consecutive control ticks -> hard cutoff.
OVERLOAD_FACTOR = _env_float("GOODWE_OVERLOAD_FACTOR", 1.05)
OVERLOAD_GRACE_TICKS = _env_int("GOODWE_OVERLOAD_GRACE_TICKS", 3)
# Overload latch clears once load stays under limit * this factor for the cooldown.
OVERLOAD_RECOVERY_FACTOR = _env_float("GOODWE_OVERLOAD_RECOVERY_FACTOR", 0.90)
OVERLOAD_COOLDOWN_S = _env_float("GOODWE_OVERLOAD_COOLDOWN_S", 10.0)

# --- Control loop ----------------------------------------------------------
CONTROL_INTERVAL_S = _env_float("GOODWE_CONTROL_INTERVAL_S", 1.0)
TELEMETRY_PERSIST_INTERVAL_S = _env_float("GOODWE_TELEMETRY_PERSIST_INTERVAL_S", 5.0)
# A station that stops talking for this long is considered offline.
STATION_TIMEOUT_S = _env_float("GOODWE_STATION_TIMEOUT_S", 6.0)
EVENT_BUFFER_SIZE = _env_int("GOODWE_EVENT_BUFFER_SIZE", 60)

# --- Default tariff (BRL/kWh, typical Brazilian residential white tariff) --
DEFAULT_PEAK_RATE = _env_float("GOODWE_PEAK_RATE", 1.35)
DEFAULT_OFF_PEAK_RATE = _env_float("GOODWE_OFF_PEAK_RATE", 0.62)
DEFAULT_SOLAR_RATE = _env_float("GOODWE_SOLAR_RATE", 0.18)
DEFAULT_PEAK_START = os.getenv("GOODWE_PEAK_START", "18:00")
DEFAULT_PEAK_END = os.getenv("GOODWE_PEAK_END", "21:00")
DEFAULT_SERVICE_FEE = _env_float("GOODWE_SERVICE_FEE", 1.50)
CURRENCY = os.getenv("GOODWE_CURRENCY", "BRL")

# --- API -------------------------------------------------------------------
# --- API -------------------------------------------------------------------
CORS_ORIGINS = os.getenv("GOODWE_CORS_ORIGINS", "*").split(",")