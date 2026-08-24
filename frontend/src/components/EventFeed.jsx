/** Audit trail of DLB decisions, tariff changes and overload trips. */
import React from 'react'
import { AlertTriangle, Info, ScrollText, ShieldAlert } from 'lucide-react'
import { chrome, status } from '../theme'
import { clockTime } from '../format'
import { Panel } from './ui'

const LEVEL = {
  INFO: { color: status.good, icon: Info },
  WARNING: { color: status.warning, icon: AlertTriangle },
  CRITICAL: { color: status.critical, icon: ShieldAlert },
}

export default function EventFeed({ events = [] }) {
  return (
    <Panel
      title="Control events"
      subtitle="Every load-balancing decision, newest first"
      icon={ScrollText}
    >
      {events.length === 0 ? (
        <p className="py-4 text-center text-xs text-ink-muted">No events yet.</p>
      ) : (
        <ol className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
          {events.map((event) => {
            const level = LEVEL[event.level] || LEVEL.INFO
            const Icon = level.icon
            return (
              <li
                key={event.id}
                className="flex items-start gap-2 rounded-lg bg-raised px-2.5 py-2"
                style={{ border: `1px solid ${chrome.border}` }}
              >
                <Icon
                  size={13}
                  className="mt-0.5 shrink-0"
                  style={{ color: level.color }}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="flex items-baseline gap-2 text-[11px] text-ink-muted">
                    <span className="tabular-nums">{clockTime(event.at)}</span>
                    <span className="truncate font-semibold uppercase tracking-wide">
                      {event.code.replace(/_/g, ' ')}
                    </span>
                    {event.connector_id ? <span>· C{event.connector_id}</span> : null}
                  </p>
                  <p className="mt-0.5 text-xs leading-snug text-ink-secondary">{event.message}</p>
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </Panel>
  )
}
