"""Station controller: the 1 Hz control loop plus every state mutation.

Everything that changes the station lives here so there is exactly one place
where billing accrual, DLB arbitration and persistence are sequenced:

    telemetry (N Hz) -> StationState  ->  tick() @1Hz -> { accrue -> balance ->
    apply -> persist -> broadcast state -> push control frame }

Route handlers never touch `StationState` directly; they call these coroutines
under a single asyncio lock, which removes any start/stop race.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from fastapi.concurrency import run_in_threadpool

from . import config, session_service as svc
from .billing_service import TariffSnapshot
from .database import session_scope
from .models import (
    ChargeMode,
    ConnectorState,
    EventLevel,
    PaymentStatus,
    SessionStatus,
    TariffWindow,
)
from .power_manager import PowerManager
from .station_state import ConnectorRuntime, StationState, station
from .ws_manager import manager

logger = logging.getLogger("goodwe.control")

# A vehicle reporting >= this SOC for `AUTO_STOP_TICKS` ends its own session.
AUTO_STOP_SOC = 99.5
AUTO_STOP_TICKS = 3


async def _db(fn, *args, **kwargs):
    """Run a `session_service` helper in a worker thread with its own session."""

    def runner():
        with session_scope() as db:
            return fn(db, *args, **kwargs)

    return await run_in_threadpool(runner)


class StationController:
    def __init__(self, state: StationState | None = None) -> None:
        self.station = state or station
        self.power = PowerManager()
        self.lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_persist = 0.0
        self._finish_ticks: dict[int, int] = {}
        self._was_online: bool | None = None

    # ------------------------------------------------------------- lifecycle
    async def bootstrap(self) -> None:
        """Load persisted tariff/limits into memory and reopen dangling sessions."""
        tariff_row = await _db(svc.get_tariff)
        self.station.set_tariff(TariffSnapshot.from_orm(tariff_row))

        cfg = await _db(svc.get_system_config)
        self.station.power_limit_w = cfg.power_limit_w
        self.station.min_current_a = cfg.min_charge_current_a
        self.station.max_current_a = cfg.max_charge_current_a
        self.station.eco_current_a = cfg.eco_current_a
        self.station.solar_priority = cfg.solar_priority

        # A session left ACTIVE by an unclean shutdown is closed as ABORTED,
        # billed from its last checkpoint — the energy was really delivered.
        for connector in self.station.connectors.values():
            row = await _db(svc.active_session_for, connector.connector_id)
            if row is not None:
                closed = await _db(
                    svc.abort_stale_session,
                    row.id,
                    self.station.tariff.service_fee,
                    "RECOVERED_AFTER_RESTART",
                )
                await self._record_event(
                    EventLevel.WARNING,
                    "SESSION_RECOVERED",
                    f"Session #{row.id} was still open at boot — aborted and billed from its "
                    f"last checkpoint ({closed.total_kwh:.3f} kWh, "
                    f"{closed.currency} {closed.total_cost:.2f}).",
                    connector.connector_id,
                )

        await self._record_event(
            EventLevel.INFO,
            "SYSTEM_READY",
            f"Controller online — site limit {self.station.power_limit_w:.0f} W, "
            f"{len(self.station.connectors)} connectors.",
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="goodwe-control-loop")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - keep the station alive
                logger.exception("control tick failed")
            await asyncio.sleep(max(config.CONTROL_INTERVAL_S - (time.monotonic() - started), 0.05))

    # ------------------------------------------------------------------ tick
    async def tick(self) -> None:
        now = time.monotonic()
        self.station.tick += 1

        await self._check_liveness()
        self._accrue_billing(now)

        decision = self.power.evaluate(
            [c.to_request() for c in self.station.connectors.values()],
            self.station.site_context(),
            now,
        )
        self.station.decision = decision
        self._apply(decision)

        for event in decision.events:
            await self._record_event(event.level, event.code, event.message, event.connector_id)

        await self._auto_stop_finished()
        await self._persist(now)

        await manager.broadcast_state(self.station.snapshot())
        await manager.send_control(self.station.control_frame())

    async def _check_liveness(self) -> None:
        online = self.station.online
        if self._was_online is None:
            self._was_online = online
        elif online != self._was_online:
            self._was_online = online
            if online:
                await self._record_event(
                    EventLevel.INFO, "STATION_ONLINE", "Telemetry stream restored."
                )
            else:
                await self._record_event(
                    EventLevel.WARNING,
                    "STATION_OFFLINE",
                    f"No telemetry for {config.STATION_TIMEOUT_S:.0f} s — "
                    "metering frozen, billing paused.",
                )
        if not online:
            # Never bill energy we cannot measure.
            for connector in self.station.connectors.values():
                connector.power_w = 0.0
                connector.current = 0.0
                connector.last_tick_monotonic = None

    def _accrue_billing(self, now: float) -> None:
        """Integrate energy + cost for every open session (see billing_service)."""
        if not self.station.online:
            return

        billing = self.station.billing
        solar_share = self.station.solar_share

        for connector in self.station.connectors.values():
            if not connector.session_active:
                continue

            previous = connector.last_tick_monotonic
            connector.last_tick_monotonic = now
            if previous is None:
                connector.last_meter_kwh = connector.energy_kwh or connector.last_meter_kwh
                continue
            dt = max(now - previous, 0.0)

            # Prefer the hardware kWh counter; fall back to integrating power
            # when the meter is absent or was reset (counter went backwards).
            delta_kwh = 0.0
            meter = connector.energy_kwh
            if meter and connector.last_meter_kwh is not None and meter >= connector.last_meter_kwh:
                delta_kwh = meter - connector.last_meter_kwh
            elif connector.power_w > 0:
                delta_kwh = billing.energy_delta_kwh(connector.power_w, dt)
            if meter:
                connector.last_meter_kwh = meter

            _, quote = billing.accrue(
                connector.acc,
                delta_kwh=delta_kwh,
                power_w=connector.power_w,
                dt_s=dt,
                solar_share=solar_share,
            )
            connector.running_cost = billing.running_total(connector.acc)
            connector.quote = quote.to_dict()

    def _apply(self, decision) -> None:
        """Copy the DLB verdict onto the runtime state."""
        volt_amp = self.station.site_context().volt_amp
        for connector_id, allocation in decision.allocations.items():
            connector = self.station.connector(connector_id)
            connector.setpoint_a = allocation.setpoint_a
            connector.allocated_w = allocation.setpoint_a * volt_amp
            connector.relay_closed = allocation.relay_closed
            connector.state = allocation.state
            connector.reason = allocation.reason
            connector.throttled = allocation.throttled

    async def _auto_stop_finished(self) -> None:
        for connector in list(self.station.connectors.values()):
            if not connector.session_active:
                self._finish_ticks.pop(connector.connector_id, None)
                continue
            full = connector.soc is not None and connector.soc >= AUTO_STOP_SOC
            if full:
                count = self._finish_ticks.get(connector.connector_id, 0) + 1
                self._finish_ticks[connector.connector_id] = count
                if count >= AUTO_STOP_TICKS:
                    await self.stop_session(
                        connector_id=connector.connector_id, reason="SOC_TARGET_REACHED"
                    )
            else:
                self._finish_ticks.pop(connector.connector_id, None)

    async def _persist(self, now: float) -> None:
        if now - self._last_persist < config.TELEMETRY_PERSIST_INTERVAL_S:
            return
        self._last_persist = now

        window = self.station.billing.quote(solar_share=self.station.solar_share).window
        rows: list[dict] = []
        for connector in self.station.connectors.values():
            if not connector.session_active and connector.power_w <= 0:
                continue
            rows.append(
                {
                    "session_id": connector.session_id,
                    "connector_id": connector.connector_id,
                    "voltage": connector.voltage,
                    "current": connector.current,
                    "power_w": connector.power_w,
                    "energy_kwh": connector.acc.total_kwh,
                    "soc": connector.soc,
                    "pv_power_w": self.station.pv_power_w,
                    "running_cost": connector.running_cost,
                    "tariff_window": window.value if isinstance(window, TariffWindow) else str(window),
                }
            )
        if rows:
            await _db(svc.insert_samples, rows)

        for connector in self.station.connectors.values():
            if connector.session_active:
                await _db(
                    svc.flush_progress,
                    connector.session_id,
                    connector.acc,
                    connector.running_cost,
                    connector.soc,
                    connector.energy_kwh or None,
                )

    async def _record_event(
        self, level: EventLevel, code: str, message: str, connector_id: int | None = None
    ) -> dict:
        event = self.station.push_event(level, code, message, connector_id)
        await _db(svc.log_event, level, code, message, connector_id)
        if level is EventLevel.CRITICAL:
            logger.warning("%s: %s", code, message)
        return event

    # -------------------------------------------------------------- commands
    async def start_session(
        self,
        connector_id: int,
        mode: ChargeMode,
        driver_label: str = "Guest",
    ) -> dict:
        async with self.lock:
            connector = self.station.connector(connector_id)
            if connector.session_active:
                raise ValueError(
                    f"Connector {connector_id} already has session #{connector.session_id}"
                )
            if self.power.overload_latched:
                raise ValueError("Overload latch is active — reset it before charging.")

            row = await _db(
                svc.open_session,
                connector_id,
                mode,
                driver_label,
                self.station.tariff,
                connector.energy_kwh or None,
                connector.soc,
            )
            connector.open_session(row.id, mode, driver_label)
            connector.state = ConnectorState.PREPARING
            connector.reason = "SESSION_STARTED"

            await self._record_event(
                EventLevel.INFO,
                "SESSION_START",
                f"Session #{row.id} started on connector {connector_id} "
                f"in {mode.value} mode for {connector.driver_label}.",
                connector_id,
            )
            payload = svc.session_to_dict(row)

        await manager.broadcast_state(self.station.snapshot())
        return payload

    async def stop_session(
        self,
        connector_id: int | None = None,
        session_id: int | None = None,
        reason: str = "OPERATOR_STOP",
        mark_paid: bool = False,
    ) -> dict:
        async with self.lock:
            connector = self._resolve_connector(connector_id, session_id)
            if connector is None or not connector.session_active:
                raise ValueError("No active session found for the given connector/session id")

            closed_id = connector.session_id
            invoice = self.station.billing.finalize(connector.acc)
            row = await _db(
                svc.close_session,
                closed_id,
                invoice,
                connector.acc,
                connector.energy_kwh or None,
                connector.soc,
                reason,
                SessionStatus.COMPLETED,
                PaymentStatus.PAID if mark_paid else None,
            )
            acc_snapshot = connector.acc
            connector.close_session()
            self._finish_ticks.pop(connector.connector_id, None)

            await self._record_event(
                EventLevel.INFO,
                "SESSION_STOP",
                f"Session #{closed_id} closed ({reason}): {invoice.total_kwh:.3f} kWh, "
                f"{invoice.currency} {invoice.total_cost:.2f}.",
                connector.connector_id,
            )
            payload = {
                "session": svc.session_to_dict(row) if row else None,
                "invoice": invoice.to_dict(),
                "billing": acc_snapshot.to_dict(),
                "breakdown": acc_snapshot.breakdown,
            }

        await manager.broadcast_state(self.station.snapshot())
        return payload

    def _resolve_connector(
        self, connector_id: int | None, session_id: int | None
    ) -> ConnectorRuntime | None:
        if connector_id is not None:
            return self.station.connectors.get(connector_id)
        if session_id is not None:
            return next(
                (c for c in self.station.connectors.values() if c.session_id == session_id), None
            )
        return next((c for c in self.station.connectors.values() if c.session_active), None)

    async def set_mode(self, connector_id: int, mode: ChargeMode) -> dict:
        async with self.lock:
            connector = self.station.connector(connector_id)
            previous = connector.mode
            connector.mode = mode
            if connector.session_active:
                await _db(svc.flush_progress, connector.session_id, connector.acc,
                          connector.running_cost, connector.soc, connector.energy_kwh or None)
            await self._record_event(
                EventLevel.INFO,
                "MODE_CHANGE",
                f"Connector {connector_id}: {previous.value} -> {mode.value}.",
                connector_id,
            )
            payload = connector.to_dict()
        await manager.broadcast_state(self.station.snapshot())
        return payload

    async def set_power_limit(
        self,
        power_limit_w: float,
        eco_current_a: float | None = None,
        max_current_a: float | None = None,
        min_current_a: float | None = None,
        solar_priority: bool | None = None,
    ) -> dict:
        async with self.lock:
            previous = self.station.power_limit_w
            row = await _db(
                svc.update_system_config,
                power_limit_w=power_limit_w,
                eco_current_a=eco_current_a,
                max_charge_current_a=max_current_a,
                min_charge_current_a=min_current_a,
                solar_priority=solar_priority,
            )
            self.station.power_limit_w = row.power_limit_w
            self.station.eco_current_a = row.eco_current_a
            self.station.max_current_a = row.max_charge_current_a
            self.station.min_current_a = row.min_charge_current_a
            self.station.solar_priority = row.solar_priority

            await self._record_event(
                EventLevel.WARNING if power_limit_w < previous else EventLevel.INFO,
                "POWER_LIMIT_CHANGE",
                f"Site power limit {previous:.0f} W -> {row.power_limit_w:.0f} W "
                f"(Eco {row.eco_current_a:.0f} A / max {row.max_charge_current_a:.0f} A).",
            )
            payload = {
                "power_limit_w": row.power_limit_w,
                "eco_current_a": row.eco_current_a,
                "max_current_a": row.max_charge_current_a,
                "min_current_a": row.min_charge_current_a,
                "solar_priority": row.solar_priority,
            }
        await manager.broadcast_state(self.station.snapshot())
        return payload

    async def set_tariff(self, **fields) -> dict:
        async with self.lock:
            row = await _db(svc.update_tariff, **fields)
            self.station.set_tariff(TariffSnapshot.from_orm(row))
            await self._record_event(
                EventLevel.INFO,
                "TARIFF_UPDATE",
                f"Tariff updated: peak {row.peak_rate:.2f} / off-peak {row.off_peak_rate:.2f} "
                f"{row.currency} per kWh, peak window "
                f"{row.current_peak_start}-{row.current_peak_end}.",
            )
            payload = self.station.tariff.to_dict()
        await manager.broadcast_state(self.station.snapshot())
        return payload

    async def reset_overload(self) -> dict:
        async with self.lock:
            event = self.power.reset_overload()
            await self._record_event(event.level, event.code, event.message)
        await manager.broadcast_state(self.station.snapshot())
        return {"overload_latched": False}


controller = StationController()
