/**
 * REST client. Same-origin relative paths: in dev the Vite proxy forwards
 * /api and /ws to FastAPI, in production FastAPI serves the built bundle.
 */
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

// The live feed needs the backend origin *explicitly*: a static host (Vercel,
// Netlify, GitHub Pages) serves the bundle from its own domain and cannot
// proxy a WebSocket upgrade through a rewrite the way it proxies /api. Falling
// back to window.location there produces a URL that resolves to index.html,
// the upgrade fails, and the dashboard silently stops receiving snapshots.
const WS_ORIGIN = (import.meta.env.VITE_WS_BASE || BASE).replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    const detail = payload?.detail
    throw new ApiError(
      typeof detail === 'string' ? detail : `Request failed (${response.status})`,
      response.status,
    )
  }
  return payload
}

/** A candidate is only usable if the browser can actually parse it. */
function isUsableWsUrl(candidate) {
  try {
    const { protocol } = new URL(candidate)
    return protocol === 'ws:' || protocol === 'wss:'
  } catch {
    return false
  }
}

/**
 * Candidate WebSocket URLs for the dashboard role, best first.
 *
 * An explicit VITE_WS_URL wins, then the same origin — correct in dev (Vite
 * proxies /ws) and in the single-process deployment where FastAPI serves the
 * bundle — then the configured backend origin, which is what a static host
 * needs. Failing over costs one round trip and keeps the same build working
 * in all three.
 *
 * Malformed candidates are dropped rather than tried: a bad value here (a URL
 * pasted as a Markdown link, a stray quote) makes `new WebSocket()` throw on
 * every attempt, and without this filter an override would pin the dashboard
 * to a URL that can never connect instead of falling back to one that works.
 */
export function telemetrySocketUrls() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const candidates = []

  const explicit = import.meta.env.VITE_WS_URL
  if (explicit) candidates.push(explicit)
  candidates.push(`${protocol}//${window.location.host}/ws/telemetry?role=dashboard`)
  if (WS_ORIGIN) {
    candidates.push(`${WS_ORIGIN.replace(/^http/, 'ws')}/ws/telemetry?role=dashboard`)
  }

  return [...new Set(candidates.filter(isUsableWsUrl))]
}

export const api = {
  state: () => request('/api/state'),
  config: () => request('/api/config'),
  health: () => request('/api/health'),

  startSession: (payload) => request('/api/sessions/start', { method: 'POST', body: payload }),
  stopSession: (payload) => request('/api/sessions/stop', { method: 'POST', body: payload }),
  history: (limit = 20) => request(`/api/sessions/history?limit=${limit}`),
  paySession: (id) =>
    request(`/api/sessions/${id}/payment`, { method: 'POST', body: { payment_status: 'PAID' } }),

  setMode: (connectorId, mode) =>
    request('/api/connectors/mode', { method: 'POST', body: { connector_id: connectorId, mode } }),
  setPowerLimit: (payload) =>
    request('/api/config/power-limit', { method: 'POST', body: payload }),
  setTariff: (payload) => request('/api/config/tariff', { method: 'POST', body: payload }),
  resetOverload: () => request('/api/system/reset-overload', { method: 'POST' }),
  telemetryHistory: (minutes = 10) => request(`/api/telemetry/history?minutes=${minutes}`),
}
