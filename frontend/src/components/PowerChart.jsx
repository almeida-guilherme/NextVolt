/**
 * Delivered power over time.
 *
 * Connector power is *stacked*, so the top edge of the stack is total EVSE draw
 * and can be read directly against the site-limit reference line. PV generation
 * rides on top as its own 2px line (same unit, same axis — never a second
 * y-axis). A table view mirrors the plot for the color-independent path.
 */
import React, { useMemo, useState } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Table2, Zap } from 'lucide-react'
import { chrome, connectorColor, ink, PV_COLOR, status, surface } from '../theme'
import { axisTime, clockTime, num } from '../format'
import { Button, Dot, Panel } from './ui'

function TooltipCard({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-lg bg-surface px-3 py-2 text-xs shadow-xl"
      style={{ border: `1px solid ${chrome.border}` }}
    >
      <p className="mb-1.5 font-semibold tabular-nums text-ink-primary">{clockTime(label)}</p>
      <ul className="space-y-1">
        {payload.map((entry) => (
          <li key={entry.dataKey} className="flex items-center gap-2">
            <Dot color={entry.color} size={7} />
            <span className="text-ink-secondary">{entry.name}</span>
            <span className="ml-auto font-semibold tabular-nums text-ink-primary">
              {num(entry.value, 2)} kW
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function PowerChart({ series = [], connectors = [], limitKw = 7.4 }) {
  const [showTable, setShowTable] = useState(false)

  const keys = useMemo(
    () =>
      connectors.map((connector) => ({
        key: `c${connector.connector_id}`,
        name: `Connector ${connector.connector_id}`,
        color: connectorColor(connector.connector_id),
      })),
    [connectors],
  )

  const tableRows = useMemo(() => series.slice(-12).reverse(), [series])

  return (
    <Panel
      title="Delivered power"
      subtitle="Connector draw stacked against the site limit · PV generation overlaid"
      icon={Zap}
      actions={
        <Button
          icon={Table2}
          onClick={() => setShowTable((value) => !value)}
          aria-pressed={showTable}
        >
          {showTable ? 'Chart' : 'Table'}
        </Button>
      }
    >
      {/* Legend is always present for >= 2 series — identity is never color-alone. */}
      <ul className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-secondary">
        {keys.map((entry) => (
          <li key={entry.key} className="flex items-center gap-1.5">
            <Dot color={entry.color} />
            {entry.name}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <Dot color={PV_COLOR} />
          Solar generation
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-px w-4"
            style={{ backgroundColor: status.critical }}
          />
          Site limit
        </li>
      </ul>

      {showTable ? (
        <div className="max-h-[236px] overflow-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="sticky top-0 bg-surface text-ink-muted">
              <tr>
                <th scope="col" className="py-1.5 pr-3 font-medium">Time</th>
                {keys.map((entry) => (
                  <th key={entry.key} scope="col" className="py-1.5 pr-3 font-medium">
                    {entry.name} (kW)
                  </th>
                ))}
                <th scope="col" className="py-1.5 pr-3 font-medium">Total (kW)</th>
                <th scope="col" className="py-1.5 font-medium">Solar (kW)</th>
              </tr>
            </thead>
            <tbody className="text-ink-secondary">
              {tableRows.length === 0 ? (
                <tr>
                  <td colSpan={keys.length + 3} className="py-4 text-center text-ink-muted">
                    Waiting for telemetry…
                  </td>
                </tr>
              ) : (
                tableRows.map((row) => (
                  <tr key={row.t} style={{ borderTop: `1px solid ${chrome.grid}` }}>
                    <td className="py-1.5 pr-3">{clockTime(row.t)}</td>
                    {keys.map((entry) => (
                      <td key={entry.key} className="py-1.5 pr-3">
                        {num(row[entry.key], 2)}
                      </td>
                    ))}
                    <td className="py-1.5 pr-3 font-semibold text-ink-primary">
                      {num(row.total, 2)}
                    </td>
                    <td className="py-1.5">{num(row.pv, 2)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="h-[236px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={chrome.grid} strokeWidth={1} vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={axisTime}
                stroke={chrome.baseline}
                tick={{ fill: ink.muted, fontSize: 11 }}
                tickLine={false}
                minTickGap={64}
              />
              {/* 'auto' lets Recharts round to clean tick values; the limit line
                  extends the domain so it is always in frame. */}
              <YAxis
                domain={[0, 'auto']}
                stroke={chrome.baseline}
                tick={{ fill: ink.muted, fontSize: 11 }}
                tickLine={false}
                width={52}
                unit=" kW"
              />
              <Tooltip
                content={<TooltipCard />}
                cursor={{ stroke: ink.muted, strokeWidth: 1 }}
                isAnimationActive={false}
              />
              {keys.map((entry) => (
                <Area
                  key={entry.key}
                  type="monotone"
                  dataKey={entry.key}
                  name={entry.name}
                  stackId="evse"
                  stroke={entry.color}
                  strokeWidth={2}
                  fill={entry.color}
                  fillOpacity={0.1}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: surface }}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
              <Line
                type="monotone"
                dataKey="pv"
                name="Solar generation"
                stroke={PV_COLOR}
                strokeWidth={2}
                strokeLinecap="round"
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: surface }}
                isAnimationActive={false}
              />
              <ReferenceLine
                y={limitKw}
                stroke={status.critical}
                strokeWidth={1}
                ifOverflow="extendDomain"
                label={{
                  value: `Site limit ${num(limitKw, 1)} kW`,
                  position: 'insideTopRight',
                  fill: ink.secondary,
                  fontSize: 11,
                }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  )
}
