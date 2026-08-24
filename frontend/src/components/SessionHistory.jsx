/** Closed sessions with their invoices — the table view for billing data. */
import React from 'react'
import { BadgeCheck, History, RefreshCw } from 'lucide-react'
import { chrome, connectorColor, status } from '../theme'
import { dateTime, duration, money, num } from '../format'
import { Badge, Button, Dot, Panel } from './ui'

const PAYMENT_COLOR = {
  PAID: status.good,
  PENDING: status.warning,
  REFUNDED: status.serious,
}

export default function SessionHistory({ history, onRefresh, onPay, busy }) {
  const items = history?.items || []
  const aggregate = history?.aggregate || {}
  const currency = aggregate.currency || 'BRL'

  return (
    <Panel
      title="Session history"
      subtitle={`${aggregate.sessions || 0} closed sessions · ${num(
        aggregate.total_kwh,
        2,
      )} kWh delivered · ${money(aggregate.total_revenue, currency)} billed`}
      icon={History}
      actions={
        <Button icon={RefreshCw} onClick={onRefresh} disabled={busy}>
          Refresh
        </Button>
      }
    >
      {aggregate.outstanding > 0 ? (
        <p className="mb-3 text-[11px] text-ink-muted">
          Outstanding balance:{' '}
          <span className="font-semibold text-ink-secondary">
            {money(aggregate.outstanding, currency)}
          </span>{' '}
          · average ticket {money(aggregate.avg_ticket, currency)}
        </p>
      ) : null}

      <div className="-mx-1 overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs tabular-nums">
          <caption className="sr-only">
            Charging sessions with energy delivered, tariff split and final cost
          </caption>
          <thead className="text-ink-muted">
            <tr>
              <th scope="col" className="px-1 py-1.5 font-medium">#</th>
              <th scope="col" className="px-1 py-1.5 font-medium">Driver</th>
              <th scope="col" className="px-1 py-1.5 font-medium">Started</th>
              <th scope="col" className="px-1 py-1.5 font-medium">Duration</th>
              <th scope="col" className="px-1 py-1.5 font-medium">Mode</th>
              <th scope="col" className="px-1 py-1.5 text-right font-medium">kWh</th>
              <th scope="col" className="px-1 py-1.5 text-right font-medium">Peak / Off / Solar</th>
              <th scope="col" className="px-1 py-1.5 text-right font-medium">Total</th>
              <th scope="col" className="px-1 py-1.5 font-medium">Payment</th>
            </tr>
          </thead>
          <tbody className="text-ink-secondary">
            {items.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-1 py-6 text-center text-ink-muted">
                  No sessions yet — start one from a connector above.
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} style={{ borderTop: `1px solid ${chrome.grid}` }}>
                  <td className="px-1 py-2">
                    <span className="flex items-center gap-1.5">
                      <Dot color={connectorColor(item.connector_id)} size={7} />
                      {item.id}
                    </span>
                  </td>
                  <td className="px-1 py-2 text-ink-primary">{item.driver_label}</td>
                  <td className="px-1 py-2">{dateTime(item.start_time)}</td>
                  <td className="px-1 py-2">{duration(item.duration_s)}</td>
                  <td className="px-1 py-2">{item.mode.replace('_', '-')}</td>
                  <td className="px-1 py-2 text-right font-semibold text-ink-primary">
                    {num(item.total_kwh, 3)}
                  </td>
                  <td className="px-1 py-2 text-right text-ink-muted">
                    {num(item.peak_kwh, 2)} / {num(item.off_peak_kwh, 2)} / {num(item.solar_kwh, 2)}
                  </td>
                  <td className="px-1 py-2 text-right font-semibold text-ink-primary">
                    {money(item.total_cost, item.currency)}
                  </td>
                  <td className="px-1 py-2">
                    {item.payment_status === 'PENDING' ? (
                      <Button icon={BadgeCheck} onClick={() => onPay(item.id)} disabled={busy}>
                        Mark paid
                      </Button>
                    ) : (
                      <Badge color={PAYMENT_COLOR[item.payment_status] || status.good}>
                        {item.payment_status}
                      </Badge>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
