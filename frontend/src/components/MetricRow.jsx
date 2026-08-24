/**
 * The live telemetry row: Voltage · Current · Active power · Energy · Cost.
 *
 * Running cost is the view's single hero figure — it is the number the driver
 * actually watches, so it gets the >=48px slot and nothing else competes.
 */
import React from 'react'
import { BatteryCharging, Gauge, Sun, Waves, Zap } from 'lucide-react'
import { money, num } from '../format'
import { seriesColors } from '../theme'
import { StatTile } from './ui'

export default function MetricRow({ snapshot }) {
  const site = snapshot?.site || {}
  const tariff = snapshot?.tariff || {}
  const currency = tariff.currency || 'BRL'
  const solarPercent = Math.round((site.solar_share || 0) * 100)

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatTile
        label="Voltage"
        value={num(site.voltage, 1)}
        unit="V"
        icon={Waves}
        hint="RMS at the meter"
      />
      <StatTile
        label="Current"
        value={num(site.total_current_a, 2)}
        unit="A"
        icon={Gauge}
        hint="Sum of all connectors"
      />
      <StatTile
        label="Active power"
        value={num((site.evse_power_w || 0) / 1000, 2)}
        unit="kW"
        icon={Zap}
        hint={`of ${num((site.power_limit_w || 0) / 1000, 1)} kW site limit`}
      />
      <StatTile
        label="Session energy"
        value={num(site.session_kwh, 3)}
        unit="kWh"
        icon={BatteryCharging}
        hint="Across active sessions"
      />
      <StatTile
        label="Solar coverage"
        value={solarPercent}
        unit="%"
        icon={Sun}
        accent={seriesColors[2]}
        hint={`PV ${num((site.pv_power_w || 0) / 1000, 2)} kW · house ${num(
          (site.house_load_w || 0) / 1000,
          2,
        )} kW`}
      />
      <div className="col-span-2 md:col-span-1">
        <StatTile
          label="Running cost"
          value={money(site.session_cost, currency)}
          size="hero"
          hint={`at ${money(tariff.effective_rate, currency)}/kWh${
            tariff.window === 'SOLAR' ? ' · solar blended' : ''
          }`}
        />
      </div>
    </div>
  )
}
