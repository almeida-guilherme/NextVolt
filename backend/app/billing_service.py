"""Billing rule engine: time-of-use pricing + incremental cost accrual.

Cost is *never* recomputed from a session total, it is integrated tick by tick:

    delta_kwh  = power_w * dt / 3_600_000        (or the meter counter delta)
    delta_cost = delta_kwh * rate_at(now)

Integrating instead of multiplying is what makes a session that starts at 17:50
and ends at 18:30 straddle the peak boundary correctly: the kWh burnt before
18:00 stay cheap forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from . import config
from .models import TariffConfig, TariffWindow

JOULES_PER_KWH = 3_600_000.0


def _parse_hhmm(raw: str, fallback: time) -> time:
    try:
        hour, minute = raw.strip().split(":")
        return time(hour=int(hour) % 24, minute=int(minute) % 60)
    except (AttributeError, ValueError):
        return fallback


@dataclass(frozen=True)
class TariffSnapshot:
    """Immutable copy of the active tariff, safe to read from the control loop."""

    peak_rate: float = config.DEFAULT_PEAK_RATE
    off_peak_rate: float = config.DEFAULT_OFF_PEAK_RATE
    solar_rate: float = config.DEFAULT_SOLAR_RATE
    peak_start: str = config.DEFAULT_PEAK_START
    peak_end: str = config.DEFAULT_PEAK_END
    service_fee: float = config.DEFAULT_SERVICE_FEE
    currency: str = config.CURRENCY

    @classmethod
    def from_orm(cls, row: TariffConfig) -> "TariffSnapshot":
        return cls(
            peak_rate=row.peak_rate,
            off_peak_rate=row.off_peak_rate,
            solar_rate=row.solar_rate,
            peak_start=row.current_peak_start,
            peak_end=row.current_peak_end,
            service_fee=row.service_fee,
            currency=row.currency,
        )

    def to_dict(self) -> dict:
        return {
            "peak_rate": self.peak_rate,
            "off_peak_rate": self.off_peak_rate,
            "solar_rate": self.solar_rate,
            "peak_start": self.peak_start,
            "peak_end": self.peak_end,
            "service_fee": self.service_fee,
            "currency": self.currency,
        }


@dataclass
class RateQuote:
    """What one kWh costs *right now*, and why."""

    window: TariffWindow
    rate: float                 # effective (blended) price per kWh
    grid_rate: float            # price of the grid share alone
    solar_rate: float
    solar_share: float          # 0..1 fraction of the load covered by PV
    currency: str

    def to_dict(self) -> dict:
        return {
            "window": self.window.value,
            "rate": round(self.rate, 4),
            "grid_rate": round(self.grid_rate, 4),
            "solar_rate": round(self.solar_rate, 4),
            "solar_share": round(self.solar_share, 4),
            "currency": self.currency,
        }


@dataclass
class CostAccumulator:
    """Per-session running totals kept in memory and flushed to SQLite."""

    total_kwh: float = 0.0
    peak_kwh: float = 0.0
    off_peak_kwh: float = 0.0
    solar_kwh: float = 0.0
    energy_cost: float = 0.0
    peak_power_w: float = 0.0
    power_integral: float = 0.0   # W*s, used for avg power
    elapsed_s: float = 0.0
    breakdown: dict = field(default_factory=dict)

    @property
    def avg_power_w(self) -> float:
        return self.power_integral / self.elapsed_s if self.elapsed_s > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "total_kwh": round(self.total_kwh, 5),
            "peak_kwh": round(self.peak_kwh, 5),
            "off_peak_kwh": round(self.off_peak_kwh, 5),
            "solar_kwh": round(self.solar_kwh, 5),
            "energy_cost": round(self.energy_cost, 4),
            "avg_power_w": round(self.avg_power_w, 1),
            "peak_power_w": round(self.peak_power_w, 1),
            "elapsed_s": round(self.elapsed_s, 1),
        }


@dataclass
class Invoice:
    energy_cost: float
    service_fee: float
    total_cost: float
    total_kwh: float
    peak_kwh: float
    off_peak_kwh: float
    solar_kwh: float
    currency: str

    def to_dict(self) -> dict:
        return {
            "energy_cost": round(self.energy_cost, 2),
            "service_fee": round(self.service_fee, 2),
            "total_cost": round(self.total_cost, 2),
            "total_kwh": round(self.total_kwh, 3),
            "peak_kwh": round(self.peak_kwh, 3),
            "off_peak_kwh": round(self.off_peak_kwh, 3),
            "solar_kwh": round(self.solar_kwh, 3),
            "currency": self.currency,
        }


class BillingService:
    """Stateless pricing engine — all state lives in `CostAccumulator`."""

    def __init__(self, tariff: TariffSnapshot | None = None) -> None:
        self.tariff = tariff or TariffSnapshot()

    # -- clock helpers -----------------------------------------------------
    def to_local(self, moment: datetime | None = None) -> datetime:
        moment = moment or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(config.TIMEZONE)

    def is_peak(self, moment: datetime | None = None) -> bool:
        """True inside the peak window, handling windows that wrap midnight."""
        local = self.to_local(moment).time()
        start = _parse_hhmm(self.tariff.peak_start, time(18, 0))
        end = _parse_hhmm(self.tariff.peak_end, time(21, 0))
        if start == end:
            return False
        if start < end:
            return start <= local < end
        return local >= start or local < end  # wraps midnight

    def seconds_until_window_change(self, moment: datetime | None = None) -> float:
        """Countdown to the next peak/off-peak flip — drives the UI badge."""
        local = self.to_local(moment)
        start = _parse_hhmm(self.tariff.peak_start, time(18, 0))
        end = _parse_hhmm(self.tariff.peak_end, time(21, 0))
        target = end if self.is_peak(moment) else start
        boundary = local.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
        if boundary <= local:
            boundary += timedelta(days=1)
        return (boundary - local).total_seconds()

    # -- pricing -----------------------------------------------------------
    def quote(self, moment: datetime | None = None, solar_share: float = 0.0) -> RateQuote:
        """Effective price of the next kWh.

        `solar_share` is the fraction of the current load covered by on-site PV;
        that share is billed at the (much cheaper) self-consumption rate, the
        remainder at the grid time-of-use rate.
        """
        peak = self.is_peak(moment)
        grid_rate = self.tariff.peak_rate if peak else self.tariff.off_peak_rate
        share = min(max(solar_share, 0.0), 1.0)
        blended = share * self.tariff.solar_rate + (1.0 - share) * grid_rate

        if share >= 0.5:
            window = TariffWindow.SOLAR
        elif peak:
            window = TariffWindow.PEAK
        else:
            window = TariffWindow.OFF_PEAK

        return RateQuote(
            window=window,
            rate=blended,
            grid_rate=grid_rate,
            solar_rate=self.tariff.solar_rate,
            solar_share=share,
            currency=self.tariff.currency,
        )

    # -- accrual -----------------------------------------------------------
    @staticmethod
    def energy_delta_kwh(power_w: float, dt_s: float) -> float:
        """Integrate instantaneous power when no cumulative meter is available."""
        if power_w <= 0 or dt_s <= 0:
            return 0.0
        return power_w * dt_s / JOULES_PER_KWH

    def accrue(
        self,
        acc: CostAccumulator,
        delta_kwh: float,
        power_w: float,
        dt_s: float,
        moment: datetime | None = None,
        solar_share: float = 0.0,
    ) -> tuple[float, RateQuote]:
        """Add one tick of energy to `acc`. Returns (delta_cost, quote)."""
        quote = self.quote(moment, solar_share)

        if dt_s > 0:
            acc.elapsed_s += dt_s
            acc.power_integral += max(power_w, 0.0) * dt_s
        acc.peak_power_w = max(acc.peak_power_w, max(power_w, 0.0))

        if delta_kwh <= 0:
            return 0.0, quote

        delta_cost = delta_kwh * quote.rate
        acc.total_kwh += delta_kwh
        acc.energy_cost += delta_cost

        # Split the kWh: the PV-covered share is tagged solar, the rest lands in
        # whichever grid window we are in.
        solar_kwh = delta_kwh * quote.solar_share
        grid_kwh = delta_kwh - solar_kwh
        acc.solar_kwh += solar_kwh
        if self.is_peak(moment):
            acc.peak_kwh += grid_kwh
        else:
            acc.off_peak_kwh += grid_kwh

        bucket = acc.breakdown.setdefault(
            quote.window.value, {"kwh": 0.0, "cost": 0.0, "rate": round(quote.rate, 4)}
        )
        bucket["kwh"] += delta_kwh
        bucket["cost"] += delta_cost
        return delta_cost, quote

    def running_total(self, acc: CostAccumulator) -> float:
        """Cost shown live on the dashboard (energy + the fixed session fee)."""
        return acc.energy_cost + self.tariff.service_fee

    def finalize(self, acc: CostAccumulator) -> Invoice:
        fee = self.tariff.service_fee if acc.total_kwh > 0 else 0.0
        return Invoice(
            energy_cost=round(acc.energy_cost, 4),
            service_fee=round(fee, 2),
            total_cost=round(acc.energy_cost + fee, 2),
            total_kwh=round(acc.total_kwh, 4),
            peak_kwh=round(acc.peak_kwh, 4),
            off_peak_kwh=round(acc.off_peak_kwh, 4),
            solar_kwh=round(acc.solar_kwh, 4),
            currency=self.tariff.currency,
        )
