import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { fmtAmount, fmtEur, fmtPct, fmtPrice } from '../lib/format'
import type { Order } from '../lib/types'

interface Props {
  market: string
  lastPrice: string | null
  holdingAmount: string | null
  reservedAmount?: string | null
  stopLossOrders: Order[]
  onChanged: () => void
}

function trimZeros(value: string): string {
  return value.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
}

export default function StopLossPanel({
  market,
  lastPrice,
  holdingAmount,
  reservedAmount,
  stopLossOrders,
  onChanged,
}: Props) {
  const { t } = useTranslation()
  const [triggerPrice, setTriggerPrice] = useState('')
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const baseAsset = market.split('-')[0]
  const owned = holdingAmount ? parseFloat(holdingAmount) : 0
  const reserved = reservedAmount ? parseFloat(reservedAmount) : 0
  const live = lastPrice ? parseFloat(lastPrice) : null

  function fillTriggerPercent(pct: number) {
    if (live === null) return
    const price = live * (1 - pct / 100)
    const decimals = price < 1 ? 8 : price < 100 ? 4 : 2
    setTriggerPrice(trimZeros(price.toFixed(decimals)))
  }

  function fillAmountPercent(pct: number) {
    if (!holdingAmount) return
    if (pct === 100) {
      setAmount(holdingAmount)
      return
    }
    setAmount(trimZeros(((owned * pct) / 100).toFixed(8)))
  }

  const estProceeds =
    amount && triggerPrice && !Number.isNaN(parseFloat(amount)) && !Number.isNaN(parseFloat(triggerPrice))
      ? parseFloat(amount) * parseFloat(triggerPrice)
      : null

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setBusy(true)
    try {
      await api<Order>('/orders', {
        method: 'POST',
        body: JSON.stringify({
          market,
          side: 'sell',
          order_type: 'stop_loss',
          amount,
          trigger_price: triggerPrice,
        }),
      })
      setSuccess(t('stopLoss.placed'))
      setAmount('')
      setTriggerPrice('')
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('stopLoss.failed'))
    } finally {
      setBusy(false)
    }
  }

  async function cancelOrder(id: number) {
    try {
      await api(`/orders/${id}`, { method: 'DELETE' })
    } catch {
      /* refresh below shows current state */
    }
    onChanged()
  }

  const inputClass =
    'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-mono outline-none focus:border-amber-500'

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="font-semibold">{t('stopLoss.title', { asset: baseAsset })}</h3>
      <p className="mt-1 text-xs text-slate-500">{t('stopLoss.description')}</p>

      {stopLossOrders.length > 0 && (
        <ul className="mt-3 space-y-2">
          {stopLossOrders.map((o) => {
            const trigger = o.trigger_price ? parseFloat(o.trigger_price) : null
            const distance =
              trigger !== null && live !== null && live > 0 ? ((trigger - live) / live) * 100 : null
            return (
              <li
                key={o.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm"
              >
                <span>
                  <span className="font-mono">{fmtAmount(o.amount)}</span>{' '}
                  <span className="text-slate-400">{baseAsset}</span>
                  <span className="mx-2 text-slate-600">·</span>
                  {t('stopLoss.triggerAt')}{' '}
                  <span className="font-mono text-amber-400">{fmtPrice(o.trigger_price)}</span>
                  {distance !== null && (
                    <span className="ml-2 text-xs text-slate-500">
                      ({fmtPct(distance)} {t('stopLoss.fromCurrent')})
                    </span>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => cancelOrder(o.id)}
                  className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 md:px-2 md:py-0.5"
                >
                  {t('common.cancel')}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <form onSubmit={onSubmit} className="mt-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                {t('stopLoss.triggerPriceLabel')}
              </label>
              {live !== null && (
                <span className="flex gap-1">
                  {[5, 10, 15].map((pct) => (
                    <button
                      key={pct}
                      type="button"
                      onClick={() => fillTriggerPercent(pct)}
                      title={t('stopLoss.triggerPctTitle', { pct })}
                      className="rounded border border-slate-700 px-2 py-1 text-[10px] font-medium text-slate-400 transition-colors hover:border-amber-500 hover:text-amber-400 md:px-1.5 md:py-0.5"
                    >
                      -{pct}%
                    </button>
                  ))}
                </span>
              )}
            </div>
            <input
              type="number"
              step="any"
              min="0"
              required
              value={triggerPrice}
              onChange={(e) => setTriggerPrice(e.target.value)}
              placeholder={lastPrice ?? ''}
              className={inputClass}
            />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                {t('stopLoss.amountLabel', { asset: baseAsset })}
              </label>
              {(owned > 0 || reserved > 0) && (
                <span className="flex gap-1">
                  {[25, 50, 75, 100].map((pct) => (
                    <button
                      key={pct}
                      type="button"
                      disabled={owned <= 0}
                      onClick={() => fillAmountPercent(pct)}
                      title={
                        owned <= 0
                          ? t('orderForm.allReserved', { asset: baseAsset })
                          : t('stopLoss.protectPctTitle', { pct })
                      }
                      className="rounded border border-slate-700 px-2 py-1 text-[10px] font-medium text-slate-400 transition-colors enabled:hover:border-amber-500 enabled:hover:text-amber-400 disabled:cursor-not-allowed disabled:opacity-40 md:px-1.5 md:py-0.5"
                    >
                      {pct}%
                    </button>
                  ))}
                </span>
              )}
            </div>
            <input
              type="number"
              step="any"
              min="0"
              required
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder={t('orderForm.amountPlaceholder')}
              className={inputClass}
            />
          </div>
        </div>

        {estProceeds !== null && (
          <p className="mt-2 text-xs text-slate-400">
            {t('stopLoss.estProceeds', { value: fmtEur(estProceeds) })}
          </p>
        )}

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        {success && <p className="mt-3 text-sm text-emerald-400">{success}</p>}

        <button
          type="submit"
          disabled={busy}
          className="mt-4 w-full rounded-md bg-amber-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-amber-500 disabled:opacity-50 sm:w-auto sm:px-8"
        >
          {busy ? t('orderForm.placing') : t('stopLoss.placeButton', { asset: baseAsset })}
        </button>
      </form>
    </div>
  )
}
