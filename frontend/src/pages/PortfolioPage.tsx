import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtAmount, fmtEur, fmtPrice } from '../lib/format'
import type { Portfolio } from '../lib/types'

export default function PortfolioPage() {
  const { t } = useTranslation()
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { prices } = usePrices()

  useEffect(() => {
    api<Portfolio>('/portfolio').then(setPortfolio).catch((e) => setError(e.message))
    const timer = setInterval(() => {
      api<Portfolio>('/portfolio').then(setPortfolio).catch(() => {})
    }, 10000)
    return () => clearInterval(timer)
  }, [])

  // Re-value holdings with the live price stream between refreshes.
  const live = useMemo(() => {
    if (!portfolio) return null
    let holdingsValue = 0
    const holdings = portfolio.holdings.map((h) => {
      const liveLast = h.market ? prices[h.market]?.last : null
      const price = liveLast ?? h.current_price
      const value = price !== null ? parseFloat(h.amount) * parseFloat(price) : null
      if (value !== null) holdingsValue += value
      return { ...h, current_price: price, live_value: value }
    })
    const cash = parseFloat(portfolio.balance_eur)
    const reserved = parseFloat(portfolio.reserved_eur)
    return { holdings, holdingsValue, cash, reserved, total: cash + reserved + holdingsValue }
  }, [portfolio, prices])

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
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h2 className="font-semibold">{t('portfolio.yourCrypto')}</h2>
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
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {live.holdings.map((h) => (
                <tr key={h.asset} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                  <td className="px-4 py-2.5 font-medium">{h.asset}</td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtAmount(h.amount)}</td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtPrice(h.current_price)}</td>
                  <td className="px-4 py-2.5 text-right font-mono">{fmtEur(h.live_value)}</td>
                  <td className="px-4 py-2.5 text-right">
                    {h.market && (
                      <Link
                        to={`/trade/${h.market}`}
                        className="text-xs text-amber-400 hover:underline"
                      >
                        {t('portfolio.tradeLink')}
                      </Link>
                    )}
                  </td>
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
