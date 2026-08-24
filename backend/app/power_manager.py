"""Dynamic Load Balancing (DLB) + overload protection.

The site has one hard ceiling (`power_limit_w`, e.g. the 7.4 kW main breaker).
Every control tick the manager:

1. turns each connector's *mode* into a current **request** (Eco / Fast / Solar /
   Off-Peak, with an SOC taper on top);
2. subtracts the building load measured upstream, leaving the real EVSE budget;
3. shares that budget with **progressive water-filling** — small requests are
   served in full, the surplus is split evenly between the greedy ones;
4. enforces the IEC 61851 6 A floor: a connector that cannot be given 6 A is
   *suspended* rather than starved, oldest session keeps priority (FIFO);
5. latches a hard **cutoff** if the measured load stays above the ceiling for
   `OVERLOAD_GRACE_TICKS` in a row, and auto-rearms after a cooldown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from . import config
from .models import ChargeMode, ConnectorState, EventLevel

# SOC taper: (soc_threshold, fraction of the mode cap still allowed)
SOC_TAPER = ((98.0, 0.0), (90.0, 0.35), (80.0, 0.65))


@dataclass
class ConnectorRequest:
    """What one connector wants this tick."""

    connector_id: int
    mode: ChargeMode = ChargeMode.ECO
    session_active: bool = False
    vehicle_connected: bool = False
    soc: float | None = None
    started_at: datetime | None = None      # FIFO priority key


@dataclass
class SiteContext:
    """Electrical envelope + upstream measurements for this tick."""

    power_limit_w: float = config.DEFAULT_POWER_LIMIT_W
    voltage: float = config.NOMINAL_VOLTAGE
    phases: int = config.PHASES
    min_current_a: float = config.MIN_CHARGE_CURRENT_A
    max_current_a: float = config.MAX_CHARGE_CURRENT_A
    eco_current_a: float = 10.0
    solar_priority: bool = True
    pv_power_w: float = 0.0
    house_load_w: float = 0.0          # non-EVSE building load at the main breaker
    measured_total_w: float = 0.0      # everything behind the breaker
    is_peak: bool = False

    @property
    def volt_amp(self) -> float:
        """Watts per amp of setpoint (voltage x phases), floored to stay safe."""
        return max(self.voltage, 90.0) * max(self.phases, 1)

    def amps(self, watts: float) -> float:
        return max(watts, 0.0) / self.volt_amp

    def watts(self, amps: float) -> float:
        return max(amps, 0.0) * self.volt_amp


@dataclass
class ConnectorAllocation:
    connector_id: int
    requested_a: float = 0.0
    setpoint_a: float = 0.0
    relay_closed: bool = False
    state: ConnectorState = ConnectorState.AVAILABLE
    reason: str = "IDLE"
    throttled: bool = False

    def to_dict(self, volt_amp: float) -> dict:
        return {
            "connector_id": self.connector_id,
            "requested_a": round(self.requested_a, 2),
            "setpoint_a": round(self.setpoint_a, 2),
            "allocated_w": round(self.setpoint_a * volt_amp, 1),
            "relay_closed": self.relay_closed,
            "state": self.state.value,
            "reason": self.reason,
            "throttled": self.throttled,
        }


@dataclass
class DlbEvent:
    level: EventLevel
    code: str
    message: str
    connector_id: int | None = None


@dataclass
class Decision:
    allocations: dict[int, ConnectorAllocation] = field(default_factory=dict)
    events: list[DlbEvent] = field(default_factory=list)
    budget_w: float = 0.0
    allocated_w: float = 0.0
    headroom_w: float = 0.0
    utilization: float = 0.0
    overload: bool = False
    curtailed: bool = False
    suspended_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "budget_w": round(self.budget_w, 1),
            "allocated_w": round(self.allocated_w, 1),
            "headroom_w": round(self.headroom_w, 1),
            "utilization": round(self.utilization, 4),
            "overload": self.overload,
            "curtailed": self.curtailed,
            "suspended_connectors": self.suspended_ids,
        }


class PowerManager:
    """Stateful DLB engine — keeps the overload latch and de-duplicates events."""

    def __init__(self) -> None:
        self._over_ticks = 0
        self._under_since: float | None = None
        self.overload_latched = False
        self._last_state: dict[int, ConnectorState] = {}
        self._last_setpoint: dict[int, float] = {}

    # ------------------------------------------------------------------ mode
    def _mode_cap_a(
        self, req: ConnectorRequest, ctx: SiteContext, solar_share_a: float
    ) -> tuple[float, str]:
        """Translate a charging mode into a current ceiling + the limiting cause."""
        if req.mode is ChargeMode.FAST:
            return ctx.max_current_a, "MODE_FAST"

        if req.mode is ChargeMode.ECO:
            return min(ctx.eco_current_a, ctx.max_current_a), "MODE_ECO"

        if req.mode is ChargeMode.OFF_PEAK:
            if ctx.is_peak:
                return 0.0, "TARIFF_PEAK_WINDOW"
            return ctx.max_current_a, "MODE_OFF_PEAK"

        # SOLAR: ride the PV surplus only.
        if solar_share_a < ctx.min_current_a:
            return 0.0, "SOLAR_SURPLUS_TOO_LOW"
        return min(solar_share_a, ctx.max_current_a), "MODE_SOLAR"

    def _taper_a(self, cap_a: float, soc: float | None, ctx: SiteContext) -> tuple[float, str]:
        """Batteries taper near full — mirror that so the DLB frees amps early."""
        if soc is None:
            return cap_a, ""
        for threshold, fraction in SOC_TAPER:
            if soc >= threshold:
                if fraction == 0.0:
                    return 0.0, "BATTERY_FULL"
                return min(cap_a, ctx.max_current_a * fraction), "SOC_TAPER"
        return cap_a, ""

    # --------------------------------------------------------------- sharing
    @staticmethod
    def _water_fill(requests: dict[int, float], budget_a: float) -> dict[int, float]:
        """Progressive water-filling: max-min fair share of `budget_a`."""
        alloc: dict[int, float] = {}
        remaining = max(budget_a, 0.0)
        ordered = sorted(requests, key=lambda cid: requests[cid])
        for index, cid in enumerate(ordered):
            share = remaining / (len(ordered) - index)
            grant = min(requests[cid], share)
            alloc[cid] = grant
            remaining -= grant
        return alloc

    def _share_budget(
        self,
        requests: dict[int, float],
        budget_a: float,
        ctx: SiteContext,
        priority: list[int],
    ) -> tuple[dict[int, float], list[int]]:
        """Water-fill, then drop the lowest priority connectors until every
        remaining one clears the 6 A floor. Returns (alloc, suspended_ids)."""
        pool = dict(requests)
        suspended: list[int] = []
        # `priority` is best-first; we always sacrifice from the tail.
        drop_order = [cid for cid in reversed(priority) if cid in pool]

        while pool:
            alloc = self._water_fill(pool, budget_a)
            if all(value >= ctx.min_current_a - 1e-6 for value in alloc.values()):
                return alloc, suspended
            victim = next((cid for cid in drop_order if cid in pool), None)
            if victim is None:
                break
            pool.pop(victim)
            drop_order.remove(victim)
            suspended.append(victim)

        return {}, suspended + [cid for cid in requests if cid not in suspended]

    # -------------------------------------------------------------- overload
    def _update_overload(self, ctx: SiteContext, now_monotonic: float) -> list[DlbEvent]:
        events: list[DlbEvent] = []
        threshold = ctx.power_limit_w * config.OVERLOAD_FACTOR
        recovery = ctx.power_limit_w * config.OVERLOAD_RECOVERY_FACTOR

        if ctx.measured_total_w > threshold:
            self._over_ticks += 1
            self._under_since = None
            if not self.overload_latched and self._over_ticks >= config.OVERLOAD_GRACE_TICKS:
                self.overload_latched = True
                events.append(
                    DlbEvent(
                        EventLevel.CRITICAL,
                        "OVERLOAD_CUTOFF",
                        f"Hard cutoff: site load {ctx.measured_total_w:.0f} W exceeded "
                        f"{threshold:.0f} W for {self._over_ticks} consecutive ticks. "
                        "All connectors opened.",
                    )
                )
        else:
            self._over_ticks = 0
            if self.overload_latched:
                if ctx.measured_total_w <= recovery:
                    if self._under_since is None:
                        self._under_since = now_monotonic
                    elif now_monotonic - self._under_since >= config.OVERLOAD_COOLDOWN_S:
                        self.overload_latched = False
                        self._under_since = None
                        events.append(
                            DlbEvent(
                                EventLevel.INFO,
                                "OVERLOAD_CLEARED",
                                f"Load back under {recovery:.0f} W for "
                                f"{config.OVERLOAD_COOLDOWN_S:.0f} s — connectors re-armed.",
                            )
                        )
                else:
                    self._under_since = None
        return events

    def reset_overload(self) -> DlbEvent:
        """Manual latch reset exposed through the API."""
        self.overload_latched = False
        self._over_ticks = 0
        self._under_since = None
        return DlbEvent(EventLevel.INFO, "OVERLOAD_RESET", "Overload latch reset by operator.")

    # ------------------------------------------------------------------ main
    def evaluate(
        self,
        connectors: list[ConnectorRequest],
        ctx: SiteContext,
        now_monotonic: float = 0.0,
    ) -> Decision:
        decision = Decision()
        decision.events.extend(self._update_overload(ctx, now_monotonic))

        # 1. EVSE budget = breaker ceiling minus whatever the building is drawing.
        budget_w = max(ctx.power_limit_w - max(ctx.house_load_w, 0.0), 0.0)
        budget_a = ctx.amps(budget_w)
        decision.budget_w = budget_w
        decision.overload = self.overload_latched

        # 2. Per-connector request.
        solar_connectors = [
            c for c in connectors if c.mode is ChargeMode.SOLAR and c.session_active
        ]
        pv_surplus_w = max(ctx.pv_power_w - max(ctx.house_load_w, 0.0), 0.0)
        solar_share_a = (
            ctx.amps(pv_surplus_w) / len(solar_connectors) if solar_connectors else 0.0
        )

        requests: dict[int, float] = {}
        reasons: dict[int, str] = {}
        for req in connectors:
            alloc = ConnectorAllocation(connector_id=req.connector_id)
            decision.allocations[req.connector_id] = alloc

            if self.overload_latched:
                alloc.state = ConnectorState.FAULTED
                alloc.reason = "OVERLOAD_CUTOFF"
                continue
            if not req.session_active:
                alloc.state = (
                    ConnectorState.PREPARING if req.vehicle_connected else ConnectorState.AVAILABLE
                )
                alloc.reason = "NO_ACTIVE_SESSION"
                continue
            if not req.vehicle_connected:
                alloc.state = ConnectorState.SUSPENDED_EVSE
                alloc.reason = "VEHICLE_DISCONNECTED"
                continue

            cap_a, cause = self._mode_cap_a(req, ctx, solar_share_a)
            cap_a, taper_cause = self._taper_a(cap_a, req.soc, ctx)
            if taper_cause:
                cause = taper_cause

            alloc.requested_a = round(cap_a, 2)
            if cap_a < ctx.min_current_a:
                alloc.state = (
                    ConnectorState.FINISHING
                    if cause == "BATTERY_FULL"
                    else ConnectorState.SUSPENDED_EVSE
                )
                alloc.reason = cause
                continue

            requests[req.connector_id] = min(cap_a, ctx.max_current_a)
            reasons[req.connector_id] = cause

        # 3. Fair share. FIFO: the session that started first keeps its amps.
        priority = sorted(
            requests,
            key=lambda cid: next(
                (c.started_at for c in connectors if c.connector_id == cid), None
            )
            or datetime.max,
        )

        granted: dict[int, float] = {}
        suspended: list[int] = []
        pending = dict(requests)

        # 3a. Solar priority: PV surplus is reserved for SOLAR-mode connectors
        # before anything else, so a Fast session cannot eat the free energy.
        if ctx.solar_priority:
            solar_ids = {c.connector_id for c in solar_connectors}
            solar_requests = {cid: amps for cid, amps in pending.items() if cid in solar_ids}
            if solar_requests:
                solar_granted, solar_suspended = self._share_budget(
                    solar_requests, budget_a, ctx, priority
                )
                granted.update(solar_granted)
                suspended.extend(solar_suspended)
                budget_a = max(budget_a - sum(solar_granted.values()), 0.0)
                pending = {cid: amps for cid, amps in pending.items() if cid not in solar_requests}

        # 3b. Everyone else shares whatever is left.
        rest_granted, rest_suspended = self._share_budget(pending, budget_a, ctx, priority)
        granted.update(rest_granted)
        suspended.extend(rest_suspended)

        for cid, amps in granted.items():
            alloc = decision.allocations[cid]
            # Floor (never round up) — the site limit is a breaker, not a target.
            amps = math.floor(min(amps, ctx.max_current_a) * 100) / 100
            alloc.setpoint_a = amps
            alloc.relay_closed = True
            # 0.5 A deadband: a house-load wobble must not flap the UI state.
            throttled = amps < requests[cid] - 0.5
            alloc.throttled = throttled
            alloc.state = ConnectorState.THROTTLED if throttled else ConnectorState.CHARGING
            alloc.reason = "DLB_SHARED_LIMIT" if throttled else reasons[cid]

        for cid in suspended:
            alloc = decision.allocations[cid]
            alloc.state = ConnectorState.SUSPENDED_EVSE
            alloc.reason = "DLB_QUEUED_NO_HEADROOM"
            alloc.setpoint_a = 0.0
            alloc.relay_closed = False

        decision.suspended_ids = sorted(suspended)
        decision.allocated_w = sum(
            ctx.watts(a.setpoint_a) for a in decision.allocations.values()
        )
        decision.headroom_w = max(budget_w - decision.allocated_w, 0.0)
        decision.utilization = (
            (ctx.measured_total_w / ctx.power_limit_w) if ctx.power_limit_w > 0 else 0.0
        )
        decision.curtailed = bool(suspended) or any(
            a.throttled for a in decision.allocations.values()
        )

        decision.events.extend(self._diff_events(decision, ctx))
        return decision

    # ----------------------------------------------------------- event diff
    def _diff_events(self, decision: Decision, ctx: SiteContext) -> list[DlbEvent]:
        """Emit an event only when a connector actually changes behaviour."""
        events: list[DlbEvent] = []
        for cid, alloc in decision.allocations.items():
            previous = self._last_state.get(cid)
            prev_setpoint = self._last_setpoint.get(cid, 0.0)

            if previous != alloc.state:
                if alloc.state is ConnectorState.SUSPENDED_EVSE and alloc.reason.startswith("DLB"):
                    events.append(
                        DlbEvent(
                            EventLevel.WARNING,
                            "DLB_SUSPEND",
                            f"Connector {cid} queued: no room for "
                            f"{ctx.min_current_a:.0f} A inside the "
                            f"{ctx.power_limit_w:.0f} W site limit.",
                            cid,
                        )
                    )
                elif alloc.state is ConnectorState.THROTTLED:
                    events.append(
                        DlbEvent(
                            EventLevel.WARNING,
                            "DLB_THROTTLE",
                            f"Connector {cid} throttled to {alloc.setpoint_a:.1f} A "
                            f"(asked {alloc.requested_a:.1f} A).",
                            cid,
                        )
                    )
                elif alloc.state is ConnectorState.CHARGING and previous in (
                    ConnectorState.THROTTLED,
                    ConnectorState.SUSPENDED_EVSE,
                ):
                    events.append(
                        DlbEvent(
                            EventLevel.INFO,
                            "DLB_RESTORE",
                            f"Connector {cid} restored to {alloc.setpoint_a:.1f} A.",
                            cid,
                        )
                    )
                elif alloc.state is ConnectorState.FINISHING:
                    events.append(
                        DlbEvent(
                            EventLevel.INFO,
                            "SOC_COMPLETE",
                            f"Connector {cid} reached target SOC — tapering off.",
                            cid,
                        )
                    )
            elif (
                alloc.state is ConnectorState.THROTTLED
                and abs(alloc.setpoint_a - prev_setpoint) >= 2.0
            ):
                events.append(
                    DlbEvent(
                        EventLevel.INFO,
                        "DLB_ADJUST",
                        f"Connector {cid} setpoint moved "
                        f"{prev_setpoint:.1f} A -> {alloc.setpoint_a:.1f} A.",
                        cid,
                    )
                )

            self._last_state[cid] = alloc.state
            self._last_setpoint[cid] = alloc.setpoint_a
        return events
