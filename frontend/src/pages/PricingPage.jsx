/**
 * Pricing — the tariff configuration page.
 *
 * A single panel, centred rather than stretched: the form has six fields and
 * reads badly at full dashboard width.
 */
import React from 'react'
import TariffPanel from '../components/TariffPanel'

export default function PricingPage({ snapshot, onSave, busy }) {
  return (
    <div className="mx-auto w-full max-w-3xl">
      <TariffPanel snapshot={snapshot} onSave={onSave} busy={busy} />
    </div>
  )
}
