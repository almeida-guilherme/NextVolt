/**
 * Operations — the live control page.
 *
 * Connectors get the wide column because they are what an operator touches;
 * the site envelope and the decision log sit alongside as context.
 */
import React from 'react'
import ConnectorCard from '../components/ConnectorCard'
import SitePowerPanel from '../components/SitePowerPanel'
import EventFeed from '../components/EventFeed'

export default function OperationsPage({
  snapshot,
  currency,
  onStart,
  onStop,
  onMode,
  onSetLimit,
  onResetOverload,
  busy,
}) {
  const connectors = snapshot?.connectors || []

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* items-start: cards size to their content instead of stretching to
          match the taller sidebar column. */}
      <div className="grid grid-cols-1 items-start gap-4 lg:col-span-2 xl:grid-cols-2">
        {connectors.map((connector) => (
          <ConnectorCard
            key={connector.connector_id}
            connector={connector}
            currency={currency}
            maxCurrent={snapshot?.limits?.max_current_a || 32}
            onStart={onStart}
            onStop={onStop}
            onMode={onMode}
            busy={busy}
          />
        ))}
      </div>

      <div className="flex flex-col gap-4">
        <SitePowerPanel
          snapshot={snapshot}
          onSetLimit={onSetLimit}
          onResetOverload={onResetOverload}
          busy={busy}
        />
        <EventFeed events={snapshot?.events || []} />
      </div>
    </div>
  )
}
