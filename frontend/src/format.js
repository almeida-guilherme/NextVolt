/** Display helpers — every number the dashboard prints goes through here. */

const CURRENCY_SYMBOL = { BRL: 'R$', USD: '$', EUR: '€' }

export const money = (value, currency = 'BRL', digits = 2) =>
  `${CURRENCY_SYMBOL[currency] || currency} ${Number(value || 0).toFixed(digits)}`

export const num = (value, digits = 1) => Number(value || 0).toFixed(digits)

/** Auto-compact for stat tiles: 1,284 / 12.9K / 4.2M */
export const compact = (value, digits = 1) => {
  const n = Number(value || 0)
  const abs = Math.abs(n)
  if (abs >= 1e6) return `${(n / 1e6).toFixed(digits)}M`
  if (abs >= 1e4) return `${(n / 1e3).toFixed(digits)}K`
  return n.toLocaleString(undefined, { maximumFractionDigits: digits })
}

export const clockTime = (iso) => {
  if (!iso) return '--:--:--'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '--:--:--' : date.toLocaleTimeString()
}

export const shortTime = (iso) => {
  if (!iso) return '--:--'
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '--:--'
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/**
 * Chart axis tick. Always 24h HH:MM:SS — a bare mm:ss reads like a clock time
 * and misleads on a rolling window.
 */
export const axisTime = (value) =>
  new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

export const dateTime = (iso) => {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

/** 4210 -> "1h 10m 10s" */
export const duration = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

export const countdown = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds) || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

/** Human label for a DLB / billing reason code. */
export const reasonLabel = (code) =>
  ({
    IDLE: 'Idle',
    NO_ACTIVE_SESSION: 'No active session',
    SESSION_STARTED: 'Session starting',
    VEHICLE_DISCONNECTED: 'Cable unplugged',
    MODE_ECO: 'Eco setpoint',
    MODE_FAST: 'Full power',
    MODE_SOLAR: 'Riding PV surplus',
    MODE_OFF_PEAK: 'Off-peak window',
    TARIFF_PEAK_WINDOW: 'Waiting for off-peak',
    SOLAR_SURPLUS_TOO_LOW: 'PV surplus too low',
    SOC_TAPER: 'Battery tapering',
    BATTERY_FULL: 'Battery full',
    DLB_SHARED_LIMIT: 'Sharing the site limit',
    DLB_QUEUED_NO_HEADROOM: 'Queued — no headroom',
    OVERLOAD_CUTOFF: 'Overload cutoff',
  }[code] || code || '—')
