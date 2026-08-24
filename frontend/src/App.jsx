import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { api } from './api'
import { useStationSocket } from './hooks/useStationSocket'
import { NAV, useHashRoute } from './routes'
import { chrome, status } from './theme'
import { money, num } from './format'
import StationHeader from './components/StationHeader'
import MetricRow from './components/MetricRow'
import OperationsPage from './pages/OperationsPage'
import AnalyticsPage from './pages/AnalyticsPage'
import PricingPage from './pages/PricingPage'

const TOAST_COLOR = { info: status.good, warn: status.warning, error: status.critical }

function Toasts({ items, onDismiss }) {
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(92vw,360px)] flex-col gap-2"
      role="status"
      aria-live="polite"
    >
      {items.map((toast) => (
        <div
          key={toast.id}
          className="pointer-events-auto flex items-start gap-2 rounded-xl bg-surface px-3 py-2.5 shadow-2xl"
          style={{ border: `1px solid ${TOAST_COLOR[toast.tone]}66` }}
        >
          <span
            aria-hidden="true"
            className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: TOAST_COLOR[toast.tone] }}
          />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold text-ink-primary">{toast.title}</p>
            {toast.detail ? (
              <p className="mt-0.5 text-[11px] leading-snug text-ink-secondary">{toast.detail}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => onDismiss(toast.id)}
            className="shrink-0 rounded p-0.5 text-ink-muted transition hover:text-ink-primary"
            aria-label="Dismiss notification"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  )
}

/**
 * Shell: owns the live feed, the command handlers and the toast stack, and
 * hands them to whichever page the hash route selects. Header and the live
 * telemetry row are shared by every page.
 */
export default function App() {
  const { snapshot, series, connected } = useStationSocket()
  const route = useHashRoute()
  const [history, setHistory] = useState(null)
  const [toasts, setToasts] = useState([])
  const [busy, setBusy] = useState(false)
  const toastId = useRef(0)

  const notify = useCallback((tone, title, detail) => {
    const id = ++toastId.current
    setToasts((current) => [...current.slice(-3), { id, tone, title, detail }])
    setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 7000)
  }, [])

  const dismiss = useCallback(
    (id) => setToasts((current) => current.filter((item) => item.id !== id)),
    [],
  )

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.history(20))
    } catch (error) {
      notify('error', 'Could not load history', error.message)
    }
  }, [notify])

  useEffect(() => {
    refreshHistory()
  }, [refreshHistory])

  /** Wrap every mutation: single busy flag, uniform error surface. */
  const run = useCallback(
    async (action, onSuccess) => {
      setBusy(true)
      try {
        const result = await action()
        onSuccess?.(result)
        return result
      } catch (error) {
        notify('error', 'Command rejected', error.message)
        return null
      } finally {
        setBusy(false)
      }
    },
    [notify],
  )

  const handleStart = (connectorId, driver) => {
    const connector = snapshot?.connectors?.find((item) => item.connector_id === connectorId)
    return run(
      () =>
        api.startSession({
          connector_id: connectorId,
          mode: connector?.mode || 'ECO',
          driver_label: driver,
        }),
      (session) =>
        notify(
          'info',
          `Session #${session.id} started`,
          `Connector ${connectorId} · ${session.mode} · ${session.driver_label}`,
        ),
    )
  }

  const handleStop = (connectorId) =>
    run(
      () => api.stopSession({ connector_id: connectorId, reason: 'OPERATOR_STOP' }),
      (result) => {
        const invoice = result?.invoice
        if (invoice) {
          notify(
            'info',
            `Invoice ${money(invoice.total_cost, invoice.currency)}`,
            `${num(invoice.total_kwh, 3)} kWh · energy ${money(
              invoice.energy_cost,
              invoice.currency,
            )} + fee ${money(invoice.service_fee, invoice.currency)}`,
          )
        }
        refreshHistory()
      },
    )

  const handleMode = (connectorId, mode) => run(() => api.setMode(connectorId, mode))

  const handleLimit = (payload) =>
    run(
      () => api.setPowerLimit(payload),
      (result) =>
        notify(
          'warn',
          'Site limit updated',
          `${num(result.power_limit_w / 1000, 1)} kW · Eco ${num(result.eco_current_a, 0)} A`,
        ),
    )

  const handleTariff = (payload) =>
    run(
      () => api.setTariff(payload),
      () => notify('info', 'Tariff saved', 'Applied from the next control tick'),
    )

  const handleReset = () =>
    run(
      () => api.resetOverload(),
      () => notify('warn', 'Overload latch cleared', 'Connectors re-armed'),
    )

  const handlePay = (sessionId) =>
    run(
      () => api.paySession(sessionId),
      () => {
        notify('info', `Session #${sessionId} marked paid`)
        refreshHistory()
      },
    )

  if (!snapshot) {
    return (
      <div className="grid min-h-screen place-items-center bg-plane px-6 text-center">
        <div>
          <Loader2 size={28} className="mx-auto animate-spin text-ink-muted" aria-hidden="true" />
          <p className="mt-3 text-sm font-semibold text-ink-primary">
            Connecting to the charging station…
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            Start the API with <code className="text-ink-secondary">uvicorn app.main:app</code>, then
            the simulator with{' '}
            <code className="text-ink-secondary">python simulator/mock_esp32.py</code>.
          </p>
        </div>
      </div>
    )
  }

  const currency = snapshot.tariff?.currency || 'BRL'
  const page = NAV.find((item) => item.path === route) || NAV[0]

  return (
    <div className="min-h-screen bg-plane">
      <StationHeader
        snapshot={snapshot}
        connected={connected}
        route={route}
        onResetOverload={handleReset}
        busy={busy}
      />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-4 px-4 py-4 sm:px-6">
        <MetricRow snapshot={snapshot} />

        {route === '/analytics' ? (
          <AnalyticsPage
            snapshot={snapshot}
            series={series}
            currency={currency}
            history={history}
            onRefreshHistory={refreshHistory}
            onPay={handlePay}
            busy={busy}
          />
        ) : route === '/pricing' ? (
          <PricingPage snapshot={snapshot} onSave={handleTariff} busy={busy} />
        ) : (
          <OperationsPage
            snapshot={snapshot}
            currency={currency}
            onStart={handleStart}
            onStop={handleStop}
            onMode={handleMode}
            onSetLimit={handleLimit}
            onResetOverload={handleReset}
            busy={busy}
          />
        )}

        <footer
          className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-[11px] text-ink-muted"
          style={{ borderColor: chrome.grid }}
        >
          <span>
            {page.label} · {snapshot.station?.station_id || 'station'} · tick #{snapshot.tick}
          </span>
          <span>
            {snapshot.station?.frames_received || 0} telemetry frames · control loop{' '}
            {connected ? 'streaming' : 'offline'}
          </span>
        </footer>
      </main>

      <Toasts items={toasts} onDismiss={dismiss} />
    </div>
  )
}
