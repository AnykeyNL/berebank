import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import type { Order } from '../lib/types'

interface Props {
  market: string
  lastPrice: string | null
  holdingAmount: string | null
  onPlaced: () => void
}

function trimZeros(value: string): string {
  return value.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
}

export default function OrderForm({ market, lastPrice, holdingAmount, onPlaced }: Props) {
  const { t } = useTranslation()
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market')
  const [eurAmount, setEurAmount] = useState('')
  const [cryptoAmount, setCryptoAmount] = useState('')
  // The field the user typed in last is what the order is based on; the other
  // field is derived from it and re-derived when the price basis changes.
  const [lastEdited, setLastEdited] = useState<'eur' | 'crypto'>('eur')
  const [limitPrice, setLimitPrice] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const baseAsset = market.split('-')[0]
  const owned = holdingAmount ? parseFloat(holdingAmount) : 0

  function priceBasis(limit: string = limitPrice): number | null {
    const p = orderType === 'limit' && limit ? parseFloat(limit) : lastPrice ? parseFloat(lastPrice) : null
    return p && p > 0 ? p : null
  }

  function syncFromEur(eur: string, limit?: string) {
    setEurAmount(eur)
    setLastEdited('eur')
    const price = priceBasis(limit)
    const n = parseFloat(eur)
    if (!eur || Number.isNaN(n) || price === null) {
      setCryptoAmount('')
      return
    }
    setCryptoAmount(trimZeros((Math.floor((n / price) * 1e8) / 1e8).toFixed(8)))
  }

  function syncFromCrypto(crypto: string, limit?: string) {
    setCryptoAmount(crypto)
    setLastEdited('crypto')
    const price = priceBasis(limit)
    const n = parseFloat(crypto)
    if (!crypto || Number.isNaN(n) || price === null) {
      setEurAmount('')
      return
    }
    setEurAmount(trimZeros((n * price).toFixed(2)))
  }

  function onLimitPriceChange(value: string) {
    setLimitPrice(value)
    // Re-derive the other field against the new price.
    if (lastEdited === 'eur') syncFromEur(eurAmount, value)
    else syncFromCrypto(cryptoAmount, value)
  }

  function fillLimitPercent(offsetPct: number) {
    if (!lastPrice) return
    const price = parseFloat(lastPrice) * (1 + offsetPct / 100)
    const decimals = price < 1 ? 8 : price < 100 ? 4 : 2
    onLimitPriceChange(trimZeros(price.toFixed(decimals)))
  }

  function fillPercent(pct: number) {
    if (!holdingAmount) return
    // Use the exact stored amount for 100% to avoid rounding leftovers.
    if (pct === 100) {
      syncFromCrypto(holdingAmount)
      return
    }
    syncFromCrypto(trimZeros(((owned * pct) / 100).toFixed(8)))
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setBusy(true)
    try {
      const body: Record<string, unknown> = { market, side, order_type: orderType }
      if (orderType === 'market' && side === 'buy' && lastEdited === 'eur') {
        body.amount_quote = eurAmount
      } else {
        body.amount = cryptoAmount
      }
      if (orderType === 'limit') body.limit_price = limitPrice

      const order = await api<Order>('/orders', { method: 'POST', body: JSON.stringify(body) })
      setSuccess(
        order.status === 'filled'
          ? t('orderForm.filled', {
              price: order.filled_price,
              fee: parseFloat(order.fee_paid ?? '0').toFixed(2),
            })
          : t('orderForm.limitPlaced'),
      )
      setEurAmount('')
      setCryptoAmount('')
      onPlaced()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('orderForm.failed'))
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-mono outline-none focus:border-amber-500'

  return (
    <form onSubmit={onSubmit} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex overflow-hidden rounded-md border border-slate-700">
          {(['buy', 'sell'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className={`px-4 py-1.5 text-sm font-semibold capitalize transition-colors ${
                side === s
                  ? s === 'buy'
                    ? 'bg-emerald-600 text-white'
                    : 'bg-red-600 text-white'
                  : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              {t(`common.${s}`)}
            </button>
          ))}
        </div>
        <div className="flex overflow-hidden rounded-md border border-slate-700">
          {(['market', 'limit'] as const).map((ot) => (
            <button
              key={ot}
              type="button"
              onClick={() => setOrderType(ot)}
              className={`px-4 py-1.5 text-sm font-medium capitalize transition-colors ${
                orderType === ot ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              {t(`common.${ot}`)}
            </button>
          ))}
        </div>
      </div>

      <div className={`mt-4 grid gap-3 sm:grid-cols-2 ${orderType === 'limit' ? 'lg:grid-cols-3' : ''}`}>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              {t('orderForm.amountLabel', { asset: baseAsset })}
            </label>
            {side === 'sell' && owned > 0 && (
              <span className="flex gap-1">
                {[25, 50, 75, 100].map((pct) => (
                  <button
                    key={pct}
                    type="button"
                    onClick={() => fillPercent(pct)}
                    className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] font-medium text-slate-400 transition-colors hover:border-amber-500 hover:text-amber-400"
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
            value={cryptoAmount}
            onChange={(e) => syncFromCrypto(e.target.value)}
            placeholder={t('orderForm.amountPlaceholder')}
            className={inputClass}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
            {side === 'buy' ? t('orderForm.spendEur') : t('orderForm.receiveEur')}
          </label>
          <input
            type="number"
            step="any"
            min="0"
            value={eurAmount}
            onChange={(e) => syncFromEur(e.target.value)}
            placeholder={t('orderForm.eurPlaceholder')}
            className={inputClass}
          />
          <p className="mt-1 text-[10px] text-slate-500">{t('orderForm.syncNote', { asset: baseAsset })}</p>
        </div>
        {orderType === 'limit' && (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
                {t('orderForm.limitPriceLabel')}
              </label>
              {lastPrice && (
                <span className="flex gap-1">
                  {[2.5, 5, 7.5].map((pct) => {
                    const offset = side === 'buy' ? -pct : pct
                    return (
                      <button
                        key={pct}
                        type="button"
                        onClick={() => fillLimitPercent(offset)}
                        title={t('orderForm.limitPctTitle', { offset: `${offset > 0 ? '+' : ''}${offset}` })}
                        className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] font-medium text-slate-400 transition-colors hover:border-amber-500 hover:text-amber-400"
                      >
                        {offset > 0 ? '+' : ''}
                        {offset}%
                      </button>
                    )
                  })}
                </span>
              )}
            </div>
            <input
              type="number"
              step="any"
              min="0"
              required
              value={limitPrice}
              onChange={(e) => onLimitPriceChange(e.target.value)}
              placeholder={lastPrice ?? ''}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      {success && <p className="mt-3 text-sm text-emerald-400">{success}</p>}

      <button
        type="submit"
        disabled={busy}
        className={`mt-4 w-full rounded-md px-3 py-2 text-sm font-semibold text-white transition-colors disabled:opacity-50 sm:w-auto sm:px-8 ${
          side === 'buy' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'
        }`}
      >
        {busy
          ? t('orderForm.placing')
          : side === 'buy'
            ? t('orderForm.buyButton', { asset: baseAsset })
            : t('orderForm.sellButton', { asset: baseAsset })}
      </button>
    </form>
  )
}
