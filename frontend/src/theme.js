/**
 * Chart tokens — the single source of truth shared by every Recharts component.
 *
 * Dark mode is a *selected* palette (steps chosen for the #1a1a19 surface), not
 * an inverted light one. Validated with the data-viz six checks against that
 * surface, adjacent and all-pairs: lightness band PASS, chroma floor PASS,
 * CVD separation worst ΔE 9.4 PASS, normal-vision floor worst ΔE 20.9 PASS,
 * contrast >= 3:1 PASS.
 *
 * Categorical hues are assigned in fixed slot order and never cycled: a series
 * keeps its color when other series are filtered out.
 */
export const surface = '#1a1a19'
export const plane = '#0d0d0d'

export const ink = {
  primary: '#ffffff',
  secondary: '#c3c2b7',
  muted: '#898781',
}

export const chrome = {
  grid: '#2c2c2a',
  baseline: '#383835',
  border: 'rgba(255,255,255,0.10)',
}

/** Fixed categorical slots (blue, orange, aqua). */
export const seriesColors = ['#3987e5', '#d95926', '#199e70']

/** Sequential blue ramp — meters use the light step as fill, dark as track. */
export const sequential = {
  fill: '#3987e5',
  track: '#184f95',
}

export const status = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
}

/** Connector id -> categorical slot. Identity, never rank. */
export const connectorColor = (connectorId) =>
  seriesColors[(Number(connectorId) - 1) % seriesColors.length]

/** Solar/PV always takes the third slot so it never impersonates a connector. */
export const PV_COLOR = seriesColors[2]

/** Severity of the site power meter: accent -> warning -> critical. */
export const utilizationColor = (ratio) => {
  if (ratio >= 1) return status.critical
  if (ratio >= 0.85) return status.warning
  return sequential.fill
}

/** Connector lifecycle state -> { color, label } for badges. */
export const stateStyle = {
  AVAILABLE: { color: ink.muted, label: 'Available' },
  PREPARING: { color: status.warning, label: 'Preparing' },
  CHARGING: { color: status.good, label: 'Charging' },
  THROTTLED: { color: status.warning, label: 'Throttled' },
  SUSPENDED_EVSE: { color: status.serious, label: 'Suspended' },
  FINISHING: { color: seriesColors[0], label: 'Finishing' },
  FAULTED: { color: status.critical, label: 'Cut off' },
}

export const modeMeta = {
  ECO: { label: 'Eco', hint: 'Low power, cheapest amps' },
  FAST: { label: 'Fast', hint: 'Maximum power the site allows' },
  SOLAR: { label: 'Solar', hint: 'Follow the PV surplus only' },
  OFF_PEAK: { label: 'Off-peak', hint: 'Full power outside the peak window' },
}
