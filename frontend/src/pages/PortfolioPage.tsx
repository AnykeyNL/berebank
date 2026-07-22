import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtAmount, fmtDateTime, fmtEur, fmtPct, fmtPrice } from '../lib/format'
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
      const open = h.market ? prices[h.market]?.open : null
      const change24hPct =
        price !== null && open && parseFloat(open) !== 0
          ? ((parseFloat(price) - parseFloat(open)) / parseFloat(open)) * 100
          : null
      return { ...h, total_amount: totalAmount, current_price: price, live_value: value, change_24h_pct: change24hPct }
    })
    const cash = parseFloat(portfolio.balance_eur)
    const reserved = parseFloat(portfolio.reserved_eur)
    return { holdings, holdingsValue, cash, reserved, total: cash + reserved + holdingsValue }
  }, [portfolio, prices])

  // Live price and 24h change for the asset of an open order.
  function orderMarketInfo(market: string) {
    const p = prices[market]
    const last = p?.last ? parseFloat(p.last) : null
    const open = p?.open ? parseFloat(p.open) : null
    const change24hPct = last !== null && open !== null && open !== 0 ? ((last - open) / open) * 100 : null
    return { last, change24hPct }
  }

  function changeClassFor(change: number | null) {
    if (change === null) return 'text-slate-500'
    return change >= 0 ? 'text-emerald-400' : 'text-red-400'
  }

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
        <StatCard label={t('portfolio.assetsValue')} value={fmtEur(live.holdingsValue)} />
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <h2 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('portfolio.openLimitOrders')}</h2>
        {openOrders.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-slate-500">{t('portfolio.noOpenLimitOrders')}</p>
        ) : (
          <>
          {/* Card list on phones, table on md+ */}
          <div className="divide-y divide-slate-800/60 md:hidden">
            {openOrders.map((o) => {
              const info = orderMarketInfo(o.market)
              return (
              <div key={o.id} className="px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-baseline gap-2">
                    <Link to={`/trade/${o.market}`} className="font-medium text-amber-400 hover:underline">
                      {o.market}
                    </Link>
                    <span className={`text-xs font-medium ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t(`common.${o.side}`)}
                    </span>
                    <span className={`text-xs ${o.order_type === 'stop_loss' ? 'text-amber-400' : 'text-slate-400'}`}>
                      {t(`common.${o.order_type}`)}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => cancelOrder(o.id)}
                    className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800"
                  >
                    {t('common.cancel')}
                  </button>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.amount')}</p>
                    <p className="font-mono">{fmtAmount(o.amount)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.priceCol')}</p>
                    <p className="font-mono">
                      {fmtPrice(o.order_type === 'stop_loss' ? o.trigger_price : o.limit_price)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-slate-500">{t('portfolio.currentPrice')}</p>
                    <p className="font-mono">{fmtPrice(info.last)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.change24h')}</p>
                    <p className={`font-mono ${changeClassFor(info.change24hPct)}`}>{fmtPct(info.change24hPct)}</p>
                  </div>
                </div>
                <p className="mt-1.5 text-xs text-slate-500">{fmtDateTime(o.created_at)}</p>
              </div>
              )
            })}
          </div>
          <table className="hidden w-full text-sm md:table">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2">{t('trade.marketCol')}</th>
                <th className="px-4 py-2">{t('trade.side')}</th>
                <th className="px-4 py-2">{t('trade.typeCol')}</th>
                <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                <th className="px-4 py-2 text-right">{t('trade.priceCol')}</th>
                <th className="px-4 py-2 text-right">{t('portfolio.currentPrice')}</th>
                <th className="px-4 py-2 text-right">{t('common.change24h')}</th>
                <th className="px-4 py-2">{t('trade.placed')}</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {openOrders.map((o) => {
                const info = orderMarketInfo(o.market)
                return (
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
                  <td className="px-4 py-2 text-right font-mono">{fmtPrice(info.last)}</td>
                  <td className={`px-4 py-2 text-right font-mono ${changeClassFor(info.change24hPct)}`}>
                    {fmtPct(info.change24hPct)}
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
                )
              })}
            </tbody>
          </table>
          </>
        )}
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="flex flex-col gap-1 border-b border-slate-800 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
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
          <>
          {/* Card list on phones, table on md+ */}
          <div className="divide-y divide-slate-800/60 md:hidden">
            {live.holdings.map((h) => {
              const changeClass =
                h.change_24h_pct === null
                  ? 'text-slate-500'
                  : h.change_24h_pct >= 0
                    ? 'text-emerald-400'
                    : 'text-red-400'
              const body = (
                <>
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className={`font-medium ${h.market ? 'text-amber-400' : ''}`}>
                        {h.market ?? h.asset}
                      </span>
                      {h.name && <span className="mt-0.5 block truncate text-sm text-slate-300">{h.name}</span>}
                      {h.listing && <span className="mt-0.5 block text-xs text-slate-500">{h.listing}</span>}
                    </span>
                    <span className="shrink-0 text-right font-mono font-semibold">{fmtEur(h.live_value)}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.amount')}</p>
                      <p className="truncate font-mono">{fmtAmount(h.total_amount)}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.price')}</p>
                      <p className="truncate font-mono">{fmtPrice(h.current_price)}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.change24h')}</p>
                      <p className={`font-mono ${changeClass}`}>{fmtPct(h.change_24h_pct)}</p>
                    </div>
                  </div>
                  {parseFloat(h.reserved) > 0 && (
                    <p className="mt-1.5 text-xs text-slate-500">
                      {t('portfolio.inOpenOrders', { amount: fmtAmount(h.reserved) })}
                    </p>
                  )}
                </>
              )
              return h.market ? (
                <Link key={h.asset} to={`/trade/${h.market}`} className="block px-4 py-3 hover:bg-slate-800/30">
                  {body}
                </Link>
              ) : (
                <div key={h.asset} className="px-4 py-3">
                  {body}
                </div>
              )
            })}
          </div>
          <table className="hidden w-full text-sm md:table">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2">{t('portfolio.asset')}</th>
                <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                <th className="px-4 py-2 text-right">{t('common.price')}</th>
                <th className="px-4 py-2 text-right">{t('common.change24h')}</th>
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
                  <td
                    className={`px-4 py-2.5 text-right font-mono ${
                      h.change_24h_pct === null
                        ? 'text-slate-500'
                        : h.change_24h_pct >= 0
                          ? 'text-emerald-400'
                          : 'text-red-400'
                    }`}
                  >
                    {fmtPct(h.change_24h_pct)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtEur(h.live_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </>
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
