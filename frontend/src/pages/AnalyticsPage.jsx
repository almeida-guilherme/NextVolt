/**
 * Analytics — what happened over time.
 *
 * The power chart takes the wide column (a time series needs horizontal room);
 * cost keeps its own narrower chart rather than sharing an axis with kW.
 */
import React from 'react'
import PowerChart from '../components/PowerChart'
import CostChart from '../components/CostChart'
import SessionHistory from '../components/SessionHistory'

export default function AnalyticsPage({
  snapshot,
  series,
  currency,
  history,
  onRefreshHistory,
  onPay,
  busy,
}) {
  const tariff = snapshot?.tariff || {}

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <PowerChart
            series={series}
            connectors={snapshot?.connectors || []}
            limitKw={(snapshot?.site?.power_limit_w || 7400) / 1000}
          />
        </div>
        <CostChart
          series={series}
          currency={currency}
          peakRate={tariff.peak_rate}
          isPeak={tariff.is_peak}
        />
      </div>

      <SessionHistory
        history={history}
        onRefresh={onRefreshHistory}
        onPay={onPay}
        busy={busy}
      />
    </div>
  )
}
