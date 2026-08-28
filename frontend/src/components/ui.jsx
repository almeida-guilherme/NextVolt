/**
 * Shared primitives.
 *
 * Rule kept throughout: text always wears an ink token, never a series color.
 * Identity comes from a colored dot / line-key *beside* the text.
 */
import React from 'react'
import { chrome, ink, sequential } from '../theme'

export function Panel({ title, subtitle, icon: Icon, actions, children, className = '' }) {
  return (
    <section
      className={`rounded-xl bg-surface shadow-hairline ${className}`}
      style={{ border: `1px solid ${chrome.border}` }}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 px-4 pt-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-primary">
              {Icon ? <Icon size={16} className="shrink-0 text-ink-muted" aria-hidden="true" /> : null}
              <span className="truncate">{title}</span>
            </h2>
            {subtitle ? (
              <p className="mt-0.5 text-xs leading-snug text-ink-muted">{subtitle}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

/** Colored dot used as a legend / identity key next to text. */
export function Dot({ color, size = 8, className = '' }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${className}`}
      style={{ width: size, height: size, backgroundColor: color }}
    />
  )
}

/**
 * Stat tile: label (sentence case) · value (semibold, proportional figures) ·
 * optional unit and hint. `accent` only paints the identity dot.
 */
export function StatTile({ label, value, unit, hint, accent, icon: Icon, size = 'md' }) {
  // `hero` is the one >=48px figure a view is allowed — exactly one per screen.
  const valueClass = { md: 'text-2xl', lg: 'text-3xl', hero: 'text-5xl' }[size] || 'text-2xl'
  return (
    <div
      className="rounded-lg bg-raised px-3 py-3"
      style={{ border: `1px solid ${chrome.border}` }}
    >
      <div className="flex items-center gap-1.5">
        {accent ? <Dot color={accent} /> : null}
        {Icon ? <Icon size={13} className="text-ink-muted" aria-hidden="true" /> : null}
        <span className="truncate text-[11px] font-medium uppercase tracking-wide text-ink-muted">
          {label}
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline gap-1">
        <span className={`font-semibold leading-none text-ink-primary ${valueClass}`}>
          {value}
        </span>
        {unit ? <span className="text-xs font-medium text-ink-secondary">{unit}</span> : null}
      </div>
      {hint ? <p className="mt-1 truncate text-[11px] text-ink-muted">{hint}</p> : null}
    </div>
  )
}

/**
 * Meter. Fill carries severity; the unfilled track is a darker step of the same
 * blue ramp so the state reads across the whole bar.
 */
export function Meter({
  value = 0,
  max = 100,
  color = sequential.fill,
  // Default track = the fill's own hue, recessive. Deriving it (instead of a
  // fixed blue) keeps "same ramp" true when the fill turns warning/critical —
  // a blue track under a yellow fill reads as a second data segment.
  trackColor = `${color}33`,
  height = 8,
  label,
  valueLabel,
  markerRatio,
  markerLabel,
}) {
  const ratio = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0
  return (
    <div>
      {(label || valueLabel) && (
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          {label ? <span className="text-xs text-ink-secondary">{label}</span> : null}
          {valueLabel ? (
            <span className="text-xs font-semibold tabular-nums text-ink-primary">
              {valueLabel}
            </span>
          ) : null}
        </div>
      )}
      <div
        className="relative w-full overflow-hidden rounded-full"
        style={{ height, backgroundColor: trackColor }}
        role="meter"
        aria-valuenow={Number(value.toFixed?.(2) ?? value)}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-label={label || 'meter'}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500 ease-out"
          style={{ width: `${ratio * 100}%`, backgroundColor: color }}
        />
        {markerRatio != null && markerRatio > 0 && markerRatio < 1 ? (
          <div
            className="absolute top-0 h-full w-px"
            style={{ left: `${markerRatio * 100}%`, backgroundColor: ink.primary }}
            title={markerLabel}
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>
  )
}

/** Status pill: color + text, never color alone. */
export function Badge({ color, children, icon: Icon, className = '', title }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ${className}`}
      style={{ backgroundColor: `${color}22`, color: ink.primary, border: `1px solid ${color}66` }}
    >
      {Icon ? (
        <Icon size={11} aria-hidden="true" style={{ color }} />
      ) : (
        <Dot color={color} size={6} />
      )}
      {children}
    </span>
  )
}

const BUTTON_VARIANTS = {
  primary: 'bg-series-1 text-white hover:brightness-110',
  danger: 'bg-status-critical text-white hover:brightness-110',
  ghost: 'bg-raised text-ink-secondary hover:text-ink-primary',
}

export function Button({
  children,
  variant = 'ghost',
  icon: Icon,
  className = '',
  type = 'button',
  ...rest
}) {
  return (
    <button
      type={type}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition
        focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-series-1
        disabled:cursor-not-allowed disabled:opacity-40 ${BUTTON_VARIANTS[variant]} ${className}`}
      style={variant === 'ghost' ? { border: `1px solid ${chrome.border}` } : undefined}
      {...rest}
    >
      {Icon ? <Icon size={14} aria-hidden="true" /> : null}
      {children}
    </button>
  )
}

export function Field({ label, hint, children, htmlFor }) {
  return (
    <label className="block" htmlFor={htmlFor}>
      {/* Not uppercased: these labels carry units (kW, A) whose case is meaning. */}
      <span className="mb-1 block text-[11px] font-medium tracking-wide text-ink-muted">
        {label}
      </span>
      {children}
      {hint ? <span className="mt-1 block text-[11px] text-ink-muted">{hint}</span> : null}
    </label>
  )
}

export function Input({ className = '', ...rest }) {
  return (
    <input
      className={`w-full rounded-lg bg-plane px-2.5 py-2 text-sm tabular-nums text-ink-primary
        outline-none transition placeholder:text-ink-muted
        focus:ring-2 focus:ring-series-1 ${className}`}
      style={{ border: `1px solid ${chrome.border}` }}
      {...rest}
    />
  )
}
