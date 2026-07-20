import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtAmount, fmtDateTime, fmtEur, fmtPrice } from '../lib/format'
import type { Order, Portfolio } from '../lib/types'

export default function PortfolioPage() {
  const { t } = useTranslation()
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [error, setError] = useState<string | null>(null)
  const { prices } = usePrices()

  const refresh = () => {
    api<Portfolio>('/portfolio').then(setPortfolio).catch((e) => setError(e.message))
    api<Order[]>('/orders?status=open')
      .then((orders) =>
        setOpenOrders(orders.filter((o) => o.order_type === 'limit' || o.order_type === 'stop_loss')),
      )
      .catch(() => {})
  }

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [])

  // Re-value holdings with the live price stream between refreshes.
  const live = useMemo(() => {
    if (!portfolio) return null
    let holdingsValue = 0
    const holdings = portfolio.holdings.map((h) => {
      const liveLast = h.market ? prices[h.market]?.last : null
      const price = liveLast ?? h.current_price
      // Amounts reserved in open limit sells still count towards the value.
      const totalAmount = parseFloat(h.amount) + parseFloat(h.reserved)
      const value = price !== null ? totalAmount * parseFloat(price) : null
      if (value !== null) holdingsValue += value
      return { ...h, total_amount: totalAmount, current_price: price, live_value: value }
    })
    const cash = parseFloat(portfolio.balance_eur)
    const reserved = parseFloat(portfolio.reserved_eur)
    return { holdings, holdingsValue, cash, reserved, total: cash + reserved + holdingsValue }
  }, [portfolio, prices])

  async function cancelOrder(id: number) {
    try {
      await api(`/orders/${id}`, { method: 'DELETE' })
      refresh()
    } catch {
      refresh()
    }
  }

  if (error) return <p className="text-red-400">{error}</p>
  if (!portfolio || !live) return <p className="text-slate-400">{t('portfolio.loading')}</p>

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label={t('portfolio.totalValue')} value={fmtEur(live.total)} accent />
        <StatCard label={t('portfolio.cashEur')} value={fmtEur(live.cash)} />
        <StatCard label={t('portfolio.reserved')} value={fmtEur(live.reserved)} />
        <StatCard label={t('portfolio.cryptoValue')} value={fmtEur(live.holdingsValue)} />
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <h2 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('portfolio.openLimitOrders')}</h2>
        {openOrders.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-slate-500">{t('portfolio.noOpenLimitOrders')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2">{t('trade.marketCol')}</th>
                <th className="px-4 py-2">{t('trade.side')}</th>
                <th className="px-4 py-2">{t('trade.typeCol')}</th>
                <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                <th className="px-4 py-2 text-right">{t('trade.priceCol')}</th>
                <th className="px-4 py-2">{t('trade.placed')}</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {openOrders.map((o) => (
                <tr key={o.id} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                  <td className="px-4 py-2">
                    <Link to={`/trade/${o.market}`} className="text-amber-400 hover:underline">
                      {o.market}
                    </Link>
                  </td>
                  <td className={`px-4 py-2 font-medium ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                    {t(`common.${o.side}`)}
                  </td>
                  <td className={`px-4 py-2 ${o.order_type === 'stop_loss' ? 'text-amber-400' : 'text-slate-400'}`}>
                    {t(`common.${o.order_type}`)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">{fmtAmount(o.amount)}</td>
                  <td className="px-4 py-2 text-right font-mono">
                    {fmtPrice(o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price)}
                  </td>
                  <td className="px-4 py-2 text-slate-400">{fmtDateTime(o.created_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => cancelOrder(o.id)}
                      className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
                    >
                      {t('common.cancel')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h2 className="font-semibold">{t('portfolio.yourAssets')}</h2>
          <span className="text-xs text-slate-400">
            {t('portfolio.feeTierLine', {
              maker: portfolio.fee_tier.maker_pct,
              taker: portfolio.fee_tier.taker_pct,
              volume: fmtEur(portfolio.fee_tier.volume_30d_eur),
            })}
          </span>
        </div>
        {live.holdings.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-slate-400">
            {t('portfolio.emptyHoldings')}{' '}
            <Link to="/trade" className="text-amber-400 hover:underline">
              {t('portfolio.startTrading')}
            </Link>
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2">{t('portfolio.asset')}</th>
                <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                <th className="px-4 py-2 text-right">{t('common.price')}</th>
                <th className="px-4 py-2 text-right">{t('common.value')}</th>
              </tr>
            </thead>
            <tbody>
              {live.holdings.map((h) => (
                <tr key={h.asset} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                  <td className="px-4 py-2.5">
                    {h.market ? (
                      <Link to={`/trade/${h.market}`} className="block min-w-0 hover:opacity-90">
                        <span className="font-medium text-amber-400">{h.market}</span>
                        {h.name && <span className="mt-0.5 block text-sm text-slate-300">{h.name}</span>}
                        {h.listing && (
                          <span className="mt-0.5 block text-xs text-slate-500">{h.listing}</span>
                        )}
                      </Link>
                    ) : (
                      <span className="font-medium">{h.asset}</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {fmtAmount(h.total_amount)}
                    {parseFloat(h.reserved) > 0 && (
                      <span className="block text-xs text-slate-500">
                        {t('portfolio.inOpenOrders', { amount: fmtAmount(h.reserved) })}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtPrice(h.current_price)}</td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtEur(h.live_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${accent ? 'text-amber-400' : ''}`}>{value}</p>
    </div>
  )
}
