import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { fmtAmount, fmtDateTime, fmtDuration, fmtEur, fmtPct, fmtPrice } from '../lib/format'
import type { TradePnl } from '../lib/types'

export default function TradeHistoryPage() {
  const { t } = useTranslation()
  const [trades, setTrades] = useState<TradePnl[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<TradePnl[]>('/trades/history').then(setTrades).catch((e) => setError(e.message))
  }, [])

  const totals = useMemo(() => {
    if (!trades) return null
    let pnl = 0
    let fees = 0
    let sells = 0
    for (const tr of trades) {
      fees += parseFloat(tr.fee_eur)
      if (tr.pnl_eur !== null) {
        pnl += parseFloat(tr.pnl_eur)
        sells += 1
      }
    }
    return { pnl, fees, sells, count: trades.length }
  }, [trades])

  if (error) return <p className="text-red-400">{error}</p>
  if (!trades || !totals) return <p className="text-slate-400">{t('history.loading')}</p>

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label={t('history.realizedPnl')}
          value={fmtEur(totals.pnl)}
          tone={totals.pnl > 0 ? 'pos' : totals.pnl < 0 ? 'neg' : undefined}
        />
        <StatCard
          label={t('history.totalTrades')}
          value={t('history.totalTradesValue', { count: totals.count, closed: totals.sells })}
        />
        <StatCard label={t('history.feesPaid')} value={fmtEur(totals.fees)} />
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <h2 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('common.tradeHistory')}</h2>
        {trades.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-slate-500">{t('common.noTradesYet')}</p>
        ) : (
          <>
          {/* Card list on phones, table on md+ */}
          <div className="divide-y divide-slate-800/60 md:hidden">
            {trades.map((tr) => {
              const pnl = tr.pnl_eur !== null ? parseFloat(tr.pnl_eur) : null
              const pnlClass =
                pnl === null ? 'text-slate-500' : pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-slate-300'
              return (
                <div key={tr.id} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-baseline gap-2">
                      <Link to={`/trade/${tr.market}`} className="font-medium text-amber-400 hover:underline">
                        {tr.market}
                      </Link>
                      <span className={`text-xs font-medium ${tr.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {t(`common.${tr.side}`)}
                      </span>
                    </span>
                    <span className="text-xs text-slate-500">{fmtDateTime(tr.created_at)}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.amount')}</p>
                      <p className="truncate font-mono">{fmtAmount(tr.amount)}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.price')}</p>
                      <p className="truncate font-mono">{fmtPrice(tr.price)}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs uppercase tracking-wide text-slate-500">{t('common.value')}</p>
                      <p className="truncate font-mono">{fmtEur(tr.eur_value)}</p>
                    </div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs">
                    <span className="text-slate-500">
                      {t('common.fee')}: <span className="font-mono text-slate-400">{fmtEur(tr.fee_eur)}</span>
                    </span>
                    {pnl !== null && (
                      <span className={pnlClass}>
                        {t('history.pnl')}:{' '}
                        <span className="font-mono">
                          {fmtEur(pnl)}
                          {tr.pnl_pct !== null && ` (${fmtPct(tr.pnl_pct)})`}
                        </span>
                        <span className="ml-2 text-slate-500">
                          {t('history.held')}: {fmtDuration(tr.held_seconds)}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-2">{t('common.time')}</th>
                  <th className="px-4 py-2">{t('trade.marketCol')}</th>
                  <th className="px-4 py-2">{t('trade.side')}</th>
                  <th className="px-4 py-2 text-right">{t('common.amount')}</th>
                  <th className="px-4 py-2 text-right">{t('common.price')}</th>
                  <th className="px-4 py-2 text-right">{t('common.value')}</th>
                  <th className="px-4 py-2 text-right">{t('common.fee')}</th>
                  <th className="px-4 py-2 text-right">{t('history.pnl')}</th>
                  <th className="px-4 py-2 text-right">{t('history.pnlPct')}</th>
                  <th className="px-4 py-2 text-right">{t('history.held')}</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((tr) => {
                  const pnl = tr.pnl_eur !== null ? parseFloat(tr.pnl_eur) : null
                  const pnlClass =
                    pnl === null ? 'text-slate-500' : pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-slate-300'
                  return (
                    <tr key={tr.id} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                      <td className="px-4 py-2 whitespace-nowrap text-slate-400">{fmtDateTime(tr.created_at)}</td>
                      <td className="px-4 py-2">
                        <Link to={`/trade/${tr.market}`} className="text-amber-400 hover:underline">
                          {tr.market}
                        </Link>
                      </td>
                      <td className={`px-4 py-2 font-medium ${tr.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {t(`common.${tr.side}`)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">{fmtAmount(tr.amount)}</td>
                      <td className="px-4 py-2 text-right font-mono">{fmtPrice(tr.price)}</td>
                      <td className="px-4 py-2 text-right font-mono">{fmtEur(tr.eur_value)}</td>
                      <td className="px-4 py-2 text-right font-mono text-slate-400">{fmtEur(tr.fee_eur)}</td>
                      <td className={`px-4 py-2 text-right font-mono ${pnlClass}`}>
                        {pnl === null ? '—' : fmtEur(pnl)}
                      </td>
                      <td className={`px-4 py-2 text-right font-mono ${pnlClass}`}>
                        {tr.pnl_pct === null ? '—' : fmtPct(tr.pnl_pct)}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap text-right text-slate-400">
                        {fmtDuration(tr.held_seconds)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
        <p className="border-t border-slate-800 px-4 py-2 text-xs text-slate-500">{t('history.fifoNote')}</p>
      </div>
    </div>
  )
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className={`mt-1 text-2xl font-bold ${
          tone === 'pos' ? 'text-emerald-400' : tone === 'neg' ? 'text-red-400' : ''
        }`}
      >
        {value}
      </p>
    </div>
  )
}
