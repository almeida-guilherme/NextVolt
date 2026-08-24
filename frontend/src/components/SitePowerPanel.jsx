/**
 * Site power envelope + the Dynamic Load Balancing verdict, with the operator
 * control that sets the envelope. Meter fill carries severity
 * (accent -> warning -> critical); the track is a darker step of the same ramp.
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, Check, Scale, ShieldAlert } from 'lucide-react'
import { chrome, ink, seriesColors, status, utilizationColor } from '../theme'
import { num } from '../format'
import { Badge, Button, Dot, Field, Input, Meter, Panel } from './ui'

function Split({ color, label, value }) {
  return (
    <div className="flex items-center gap-1.5">
      <Dot color={color} size={7} />
      <span className="text-[11px] text-ink-muted">{label}</span>
      <span className="ml-auto text-[11px] font-semibold tabular-nums text-ink-secondary">
        {num(value, 2)} kW
      </span>
    </div>
  )
}

export default function SitePowerPanel({ snapshot, onSetLimit, onResetOverload, busy }) {
  const site = snapshot?.site || {}
  const limits = snapshot?.limits || {}
  const limitW = site.power_limit_w || 7400
  const measured = site.measured_total_w || 0
  const ratio = limitW > 0 ? measured / limitW : 0

  const [limitKw, setLimitKw] = useState((limitW / 1000).toFixed(1))
  const [ecoAmps, setEcoAmps] = useState(String(limits.eco_current_a ?? 10))
  const [dirty, setDirty] = useState(false)

  // Follow the server unless the operator is mid-edit.
  useEffect(() => {
    if (dirty) return
    setLimitKw((limitW / 1000).toFixed(1))
    setEcoAmps(String(limits.eco_current_a ?? 10))
  }, [limitW, limits.eco_current_a, dirty])

  const overloaded = Boolean(limits.overload_latched)
  const suspended = site.suspended_connectors || []

  const submit = async (event) => {
    event.preventDefault()
    await onSetLimit({
      power_limit_w: Math.round(Number(limitKw) * 1000),
      eco_current_a: Number(ecoAmps),
    })
    setDirty(false)
  }

  return (
    <Panel
      title="Site power & load balancing"
      subtitle="Measured behind the main breaker · the DLB shares whatever is left"
      icon={Scale}
      actions={
        overloaded ? (
          <Badge color={status.critical} icon={ShieldAlert}>
            Cutoff latched
          </Badge>
        ) : site.curtailed ? (
          <Badge color={status.warning} icon={AlertTriangle}>
            Curtailing
          </Badge>
        ) : (
          <Badge color={status.good} icon={Check}>
            Within limit
          </Badge>
        )
      }
    >
      <Meter
        value={measured}
        max={limitW}
        color={utilizationColor(ratio)}
        height={12}
        label="Total site load"
        valueLabel={`${num(measured / 1000, 2)} / ${num(limitW / 1000, 1)} kW · ${Math.round(
          ratio * 100,
        )}%`}
      />

      <div className="mt-3 space-y-1.5">
        <Split color={seriesColors[0]} label="Charging (EVSE)" value={(site.evse_power_w || 0) / 1000} />
        <Split color={ink.muted} label="Building load" value={(site.house_load_w || 0) / 1000} />
        <Split color={seriesColors[2]} label="Solar generation" value={(site.pv_power_w || 0) / 1000} />
        <Split
          color={status.good}
          label="Headroom for charging"
          value={(site.headroom_w || 0) / 1000}
        />
      </div>

      {overloaded ? (
        <div
          className="mt-3 flex items-start gap-2 rounded-lg px-3 py-2"
          style={{ backgroundColor: `${status.critical}1a`, border: `1px solid ${status.critical}55` }}
        >
          <ShieldAlert size={15} style={{ color: status.critical }} className="mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold text-ink-primary">Overload cutoff active</p>
            <p className="mt-0.5 text-[11px] text-ink-secondary">
              All relays opened because the site load stayed above the breaker limit. The latch
              re-arms itself once the load settles, or reset it now.
            </p>
          </div>
          <Button variant="danger" onClick={onResetOverload} disabled={busy}>
            Reset
          </Button>
        </div>
      ) : suspended.length ? (
        <p
          className="mt-3 rounded-lg px-3 py-2 text-[11px] text-ink-secondary"
          style={{ backgroundColor: `${status.warning}14`, border: `1px solid ${status.warning}44` }}
        >
          Connector {suspended.join(', ')} queued — there is no room for the{' '}
          {num(limits.min_current_a, 0)} A minimum inside the current limit. It resumes
          automatically as soon as headroom appears.
        </p>
      ) : null}

      <form
        onSubmit={submit}
        className="mt-4 grid grid-cols-2 gap-3 border-t pt-4"
        style={{ borderColor: chrome.grid }}
      >
        <Field label="Site limit (kW)" htmlFor="site-limit">
          <Input
            id="site-limit"
            type="number"
            min="0.5"
            max="100"
            step="0.1"
            value={limitKw}
            onChange={(event) => {
              setLimitKw(event.target.value)
              setDirty(true)
            }}
          />
        </Field>
        <Field label="Eco current (A)" htmlFor="eco-amps">
          <Input
            id="eco-amps"
            type="number"
            min="6"
            max="80"
            step="1"
            value={ecoAmps}
            onChange={(event) => {
              setEcoAmps(event.target.value)
              setDirty(true)
            }}
          />
        </Field>
        <div className="col-span-2 flex items-center justify-between gap-3">
          <p className="text-[11px] text-ink-muted">
            {num(limits.min_current_a, 0)}–{num(limits.max_current_a, 0)} A per connector ·{' '}
            {limits.phases === 3 ? '3-phase' : 'single-phase'} {num(limits.nominal_voltage, 0)} V
          </p>
          <Button type="submit" variant="primary" disabled={busy || !dirty}>
            Apply limit
          </Button>
        </div>
      </form>
    </Panel>
  )
}
