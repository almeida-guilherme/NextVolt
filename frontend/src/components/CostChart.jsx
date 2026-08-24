/**
 * Running cost over time — its own chart, on its own axis.
 *
 * Cost and power are different units, so they never share a plot (no dual-axis
 * charts, ever). Single series, therefore no legend box: the title names it.
 * The endpoint carries the one direct label.
 */
import React from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Receipt } from 'lucide-react'
import { chrome, ink, sequential, status, surface } from '../theme'
import { axisTime, clockTime, money } from '../format'
import { Panel } from './ui'

function TooltipCard({ active, payload, label, currency }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-lg bg-surface px-3 py-2 text-xs shadow-xl"
      style={{ border: `1px solid ${chrome.border}` }}
    >
      <p className="font-semibold tabular-nums text-ink-primary">{clockTime(label)}</p>
      <p className="mt-1 text-ink-secondary">
        Running cost{' '}
        <span className="font-semibold tabular-nums text-ink-primary">
          {money(payload[0].value, currency)}
        </span>
      </p>
    </div>
  )
}

export default function CostChart({ series = [], currency = 'BRL', peakRate, isPeak }) {
  const last = series.length ? series[series.length - 1] : null

  return (
    <Panel
      title="Running cost"
      subtitle={
        isPeak
          ? `Accruing at the peak rate (${money(peakRate, currency, 2)}/kWh)`
          : 'Accruing at the off-peak rate'
      }
      icon={Receipt}
    >
      <div className="h-[180px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 8, right: 56, bottom: 0, left: -18 }}>
            <defs>
              <linearGradient id="costWash" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={sequential.fill} stopOpacity={0.18} />
                <stop offset="100%" stopColor={sequential.fill} stopOpacity={0.02} />
              </linearGradient>
            </defs>
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
            {/* Ticks carry the currency so this axis is never mistaken for kW. */}
            <YAxis
              domain={[0, 'auto']}
              stroke={chrome.baseline}
              tick={{ fill: ink.muted, fontSize: 11 }}
              tickLine={false}
              width={62}
              tickFormatter={(value) => money(value, currency, 0)}
            />
            <Tooltip
              content={<TooltipCard currency={currency} />}
              cursor={{ stroke: ink.muted, strokeWidth: 1 }}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke={sequential.fill}
              strokeWidth={2}
              fill="url(#costWash)"
              activeDot={{ r: 4, strokeWidth: 2, stroke: surface }}
              isAnimationActive={false}
            />
            {last?.cost > 0 ? (
              <ReferenceLine
                y={last.cost}
                stroke="transparent"
                label={{
                  value: money(last.cost, currency),
                  position: 'right',
                  fill: ink.secondary,
                  fontSize: 11,
                }}
              />
            ) : null}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {isPeak ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: status.warning }}
          />
          Peak window active — Eco or Off-peak mode lowers the effective rate.
        </p>
      ) : null}
    </Panel>
  )
}
