import { useEffect, useMemo, useState } from 'react'
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
          <div className="overflow-x-auto">
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
                      <td className="px-4 py-2">{tr.market}</td>
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
