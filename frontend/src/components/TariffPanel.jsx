/** Time-of-use tariff editor. Takes effect on the next control tick. */
import React, { useEffect, useState } from 'react'
import { Clock, Coins } from 'lucide-react'
import { chrome, status } from '../theme'
import { countdown, money } from '../format'
import { Badge, Button, Field, Input, Panel } from './ui'

export default function TariffPanel({ snapshot, onSave, busy }) {
  const tariff = snapshot?.tariff
  const [form, setForm] = useState(null)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!tariff || dirty) return
    setForm({
      peak_rate: String(tariff.peak_rate ?? 1.35),
      off_peak_rate: String(tariff.off_peak_rate ?? 0.62),
      solar_rate: String(tariff.solar_rate ?? 0.18),
      current_peak_start: tariff.peak_start ?? '18:00',
      current_peak_end: tariff.peak_end ?? '21:00',
      service_fee: String(tariff.service_fee ?? 1.5),
    })
  }, [tariff, dirty])

  if (!form) return null

  const update = (key) => (event) => {
    setForm((current) => ({ ...current, [key]: event.target.value }))
    setDirty(true)
  }

  const submit = async (event) => {
    event.preventDefault()
    await onSave({
      peak_rate: Number(form.peak_rate),
      off_peak_rate: Number(form.off_peak_rate),
      solar_rate: Number(form.solar_rate),
      current_peak_start: form.current_peak_start,
      current_peak_end: form.current_peak_end,
      service_fee: Number(form.service_fee),
    })
    setDirty(false)
  }

  const currency = tariff.currency || 'BRL'

  return (
    <Panel
      title="Tariff & pricing"
      subtitle={`Cost = kWh x rate, integrated every second so window changes are exact`}
      icon={Coins}
      actions={
        <Badge color={tariff.is_peak ? status.warning : status.good} icon={Clock}>
          {tariff.is_peak ? 'Peak now' : 'Off-peak now'} ·{' '}
          {countdown(tariff.seconds_to_window_change)}
        </Badge>
      }
    >
      <form onSubmit={submit} className="grid grid-cols-2 gap-3">
        <Field label={`Peak rate (${currency}/kWh)`} htmlFor="peak-rate">
          <Input id="peak-rate" type="number" min="0" step="0.01" value={form.peak_rate} onChange={update('peak_rate')} />
        </Field>
        <Field label={`Off-peak rate (${currency}/kWh)`} htmlFor="off-peak-rate">
          <Input id="off-peak-rate" type="number" min="0" step="0.01" value={form.off_peak_rate} onChange={update('off_peak_rate')} />
        </Field>
        <Field label="Peak starts" htmlFor="peak-start">
          <Input id="peak-start" type="time" value={form.current_peak_start} onChange={update('current_peak_start')} />
        </Field>
        <Field label="Peak ends" htmlFor="peak-end">
          <Input id="peak-end" type="time" value={form.current_peak_end} onChange={update('current_peak_end')} />
        </Field>
        <Field label={`Solar rate (${currency}/kWh)`} htmlFor="solar-rate" hint="Applied to the PV-covered share">
          <Input id="solar-rate" type="number" min="0" step="0.01" value={form.solar_rate} onChange={update('solar_rate')} />
        </Field>
        <Field label={`Session fee (${currency})`} htmlFor="service-fee" hint="Charged once per session">
          <Input id="service-fee" type="number" min="0" step="0.1" value={form.service_fee} onChange={update('service_fee')} />
        </Field>

        <div
          className="col-span-2 flex items-center justify-between gap-3 border-t pt-3"
          style={{ borderColor: chrome.grid }}
        >
          <p className="text-[11px] text-ink-muted">
            Effective now: <span className="font-semibold text-ink-secondary">
              {money(tariff.effective_rate, currency)}/kWh
            </span>
            {tariff.solar_share > 0
              ? ` (${Math.round(tariff.solar_share * 100)}% covered by PV at ${money(
                  tariff.solar_rate,
                  currency,
                )})`
              : ''}
          </p>
          <Button type="submit" variant="primary" disabled={busy || !dirty}>
            Save tariff
          </Button>
        </div>
      </form>
    </Panel>
  )
}
