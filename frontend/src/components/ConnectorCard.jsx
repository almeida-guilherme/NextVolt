/**
 * One charge point: simulated EV with its SOC meter, the live meter readings,
 * the mode selector and the session controls.
 */
import React, { useState } from 'react'
import {
  BatteryCharging,
  Car,
  CircleDollarSign,
  Play,
  Sparkles,
  Square,
  Sun,
  Timer,
  Zap,
} from 'lucide-react'
import { chrome, connectorColor, ink, modeMeta, sequential, stateStyle, status } from '../theme'
import { duration, money, num, reasonLabel } from '../format'
import { Badge, Button, Dot, Field, Input, Meter, Panel } from './ui'

const MODE_ICONS = { ECO: Sparkles, FAST: Zap, SOLAR: Sun, OFF_PEAK: Timer }
const MODES = ['ECO', 'FAST', 'SOLAR', 'OFF_PEAK']

function Reading({ label, value, unit }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-0.5 text-sm font-semibold tabular-nums text-ink-primary">
        {value}
        {unit ? <span className="ml-1 text-xs font-medium text-ink-secondary">{unit}</span> : null}
      </p>
    </div>
  )
}

export default function ConnectorCard({
  connector,
  currency = 'BRL',
  maxCurrent = 32,
  onStart,
  onStop,
  onMode,
  busy = false,
}) {
  const [driver, setDriver] = useState('')
  const accent = connectorColor(connector.connector_id)
  const state = stateStyle[connector.state] || stateStyle.AVAILABLE
  const active = Boolean(connector.session_id)
  const soc = connector.soc ?? 0
  const elapsed = connector.started_at
    ? (Date.now() - Date.parse(connector.started_at)) / 1000
    : 0

  const socColor = soc >= 80 ? status.good : sequential.fill
  const inputId = `driver-${connector.connector_id}`

  return (
    <Panel
      title={`Connector ${connector.connector_id}`}
      subtitle={
        active
          ? `Session #${connector.session_id} · ${connector.driver_label}`
          : connector.vehicle_connected
            ? 'Vehicle plugged in — ready to start'
            : 'No vehicle detected'
      }
      icon={Car}
      actions={
        <div className="flex items-center gap-2">
          <Dot color={accent} />
          <Badge color={state.color}>{state.label}</Badge>
        </div>
      }
    >
      {/* --- simulated EV battery ------------------------------------- */}
      <div
        className="rounded-lg bg-raised p-3"
        style={{ border: `1px solid ${chrome.border}` }}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-xs text-ink-secondary">
            <BatteryCharging size={14} className="text-ink-muted" aria-hidden="true" />
            Battery state of charge
          </span>
          <span className="text-sm font-semibold tabular-nums text-ink-primary">
            {num(soc, 1)}%
          </span>
        </div>
        <Meter value={soc} max={100} color={socColor} height={12} label={null} />
        <div className="mt-2 flex items-center justify-between text-[11px] text-ink-muted">
          <span>
            {connector.battery_capacity_kwh
              ? `${num(connector.battery_capacity_kwh, 0)} kWh pack · ${num(
                  (connector.battery_capacity_kwh * soc) / 100,
                  1,
                )} kWh stored`
              : 'Pack size unknown'}
          </span>
          <span>{reasonLabel(connector.reason)}</span>
        </div>
      </div>

      {/* --- live meter ------------------------------------------------ */}
      <div className="mt-3 grid grid-cols-3 gap-3">
        <Reading label="Power" value={num(connector.power_kw, 2)} unit="kW" />
        <Reading label="Current" value={num(connector.current, 2)} unit="A" />
        <Reading label="Voltage" value={num(connector.voltage, 1)} unit="V" />
        <Reading label="Session" value={num(connector.session_kwh, 3)} unit="kWh" />
        <Reading label="Cost" value={money(connector.session_cost, currency)} />
        <Reading label="Elapsed" value={active ? duration(elapsed) : '—'} />
      </div>

      {/* Allocated setpoint vs. what the car actually pulls — the DLB story. */}
      <div className="mt-3">
        <Meter
          value={connector.current}
          max={maxCurrent}
          color={connector.throttled ? status.warning : accent}
          height={6}
          label="Allocated current"
          valueLabel={`${num(connector.current, 1)} / ${num(connector.setpoint_a, 1)} A`}
          markerRatio={connector.setpoint_a / maxCurrent}
          markerLabel={`Setpoint ${num(connector.setpoint_a, 1)} A`}
        />
      </div>

      {/* --- mode selector -------------------------------------------- */}
      <fieldset className="mt-4">
        <legend className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
          Charging mode
        </legend>
        <div className="grid grid-cols-4 gap-1.5" role="radiogroup" aria-label="Charging mode">
          {MODES.map((mode) => {
            const Icon = MODE_ICONS[mode]
            const selected = connector.mode === mode
            return (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={selected}
                title={modeMeta[mode].hint}
                disabled={busy}
                onClick={() => onMode(connector.connector_id, mode)}
                className="flex flex-col items-center gap-1 rounded-lg px-1 py-2 text-[11px] font-semibold transition
                  focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-series-1
                  disabled:opacity-40"
                style={{
                  backgroundColor: selected ? `${accent}22` : '#212120',
                  border: `1px solid ${selected ? accent : chrome.border}`,
                  color: selected ? ink.primary : ink.secondary,
                }}
              >
                <Icon size={14} aria-hidden="true" />
                {modeMeta[mode].label}
              </button>
            )
          })}
        </div>
      </fieldset>

      {/* --- session controls ----------------------------------------- */}
      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end">
        {!active ? (
          <div className="flex-1">
            <Field label="Driver" htmlFor={inputId}>
              <Input
                id={inputId}
                value={driver}
                onChange={(event) => setDriver(event.target.value)}
                placeholder="Guest"
                maxLength={64}
                autoComplete="off"
              />
            </Field>
          </div>
        ) : (
          <p className="flex-1 text-[11px] text-ink-muted">
            Billed at {money(connector.quote?.rate ?? 0, currency)}/kWh
            {connector.quote?.window ? ` · ${connector.quote.window.replace('_', '-')}` : ''}
          </p>
        )}
        {active ? (
          <Button
            variant="danger"
            icon={Square}
            onClick={() => onStop(connector.connector_id)}
            disabled={busy}
            className="sm:w-40"
          >
            Stop charging
          </Button>
        ) : (
          <Button
            variant="primary"
            icon={Play}
            onClick={() => onStart(connector.connector_id, driver.trim() || 'Guest')}
            disabled={busy}
            className="sm:w-40"
          >
            Start charging
          </Button>
        )}
      </div>

      {active ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <CircleDollarSign size={12} aria-hidden="true" />
          {num(connector.billing?.peak_kwh, 3)} kWh peak · {num(connector.billing?.off_peak_kwh, 3)}{' '}
          kWh off-peak · {num(connector.billing?.solar_kwh, 3)} kWh solar
        </p>
      ) : null}
    </Panel>
  )
}
