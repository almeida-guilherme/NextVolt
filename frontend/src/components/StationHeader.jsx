import React from 'react'
import { Activity, AlertTriangle, Sun, Wifi, WifiOff } from 'lucide-react'
import { chrome, seriesColors, status } from '../theme'
import { clockTime, countdown, money } from '../format'
import { NAV } from '../routes'
import { Badge, Button } from './ui'

/**
 * Shell header, present on every page: brand, station status, and the page nav.
 *
 * Live connection status is deliberately two-part: the browser's WebSocket link
 * and the station's own telemetry heartbeat can fail independently, and the
 * operator needs to know which one broke.
 */
export default function StationHeader({ snapshot, connected, route, onResetOverload, busy }) {
  const station = snapshot?.station
  const tariff = snapshot?.tariff
  const site = snapshot?.site
  const overloaded = Boolean(snapshot?.limits?.overload_latched)
  const stationOnline = Boolean(station?.online)

  const linkColor = connected ? status.good : status.critical
  const meterColor = stationOnline ? status.good : status.warning

  return (
    <header
      className="sticky top-0 z-10 bg-plane/95 backdrop-blur"
      style={{ borderBottom: `1px solid ${chrome.border}` }}
    >
      <div className="mx-auto max-w-[1400px] px-4 sm:px-6">
        <div className="flex flex-col gap-3 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            {/* The logo already carries the wordmark, so the image is decorative
                and the heading stays in the DOM for assistive tech only. */}
            <img
              src="/nextvolt-logo.png"
              alt=""
              className="h-10 w-auto shrink-0 sm:h-12"
              width={885}
              height={365}
            />
            <h1 className="sr-only">NextVolt</h1>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge color={linkColor} icon={connected ? Wifi : WifiOff}>
              {connected ? 'Connected' : 'Disconnected'}
            </Badge>
            <Badge color={meterColor} icon={Activity}>
              {stationOnline ? 'Meter live' : 'Meter offline'}
            </Badge>
            {tariff ? (
              <Badge color={tariff.is_peak ? status.warning : status.good}>
                {tariff.is_peak ? 'Peak' : 'Off-peak'} ·{' '}
                {money(tariff.effective_rate, tariff.currency)}/kWh
                {tariff.seconds_to_window_change != null
                  ? ` · flips in ${countdown(tariff.seconds_to_window_change)}`
                  : ''}
              </Badge>
            ) : null}
            {site?.pv_power_w > 50 ? (
              <Badge color={seriesColors[2]} icon={Sun}>
                PV {(site.pv_power_w / 1000).toFixed(2)} kW
              </Badge>
            ) : null}
            {overloaded ? (
              <Button
                variant="danger"
                icon={AlertTriangle}
                onClick={onResetOverload}
                disabled={busy}
              >
                Overload — reset
              </Button>
            ) : null}
            <span className="hidden text-xs tabular-nums text-ink-muted sm:inline">
              {clockTime(snapshot?.local_time)}
            </span>
          </div>
        </div>

        {/* Sits on the header's own bottom rule, so the active tab underlines it. */}
        <nav aria-label="Dashboard sections" className="-mb-px flex gap-1 overflow-x-auto">
          {NAV.map((item) => {
            const active = item.path === route
            const Icon = item.icon
            return (
              <a
                key={item.path}
                href={`#${item.path}`}
                title={item.hint}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-semibold transition
                  focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-series-1
                  ${
                    active
                      ? 'text-ink-primary'
                      : 'border-transparent text-ink-muted hover:text-ink-secondary'
                  }`}
                style={active ? { borderBottomColor: seriesColors[0] } : undefined}
              >
                <Icon size={14} aria-hidden="true" />
                {item.label}
              </a>
            )
          })}
        </nav>
      </div>
    </header>
  )
}
