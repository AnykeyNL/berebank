import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtEur } from '../lib/format'
import type { LeaderboardEntry } from '../lib/types'

const REFRESH_MS = 10_000

const medals = ['🥇', '🥈', '🥉']

export default function LeaderboardPage() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const [entries, setEntries] = useState<LeaderboardEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const data = await api<LeaderboardEntry[]>('/leaderboard')
        if (!cancelled) {
          setEntries(data)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    void load()
    const timer = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (error && !entries) return <p className="text-red-400">{error}</p>
  if (!entries) return <p className="text-slate-400">{t('common.loading')}</p>

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <h1 className="text-2xl font-bold text-white">{t('leaderboard.title')}</h1>
      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        {entries.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-slate-500">{t('leaderboard.empty')}</p>
        ) : (
          <>
          {/* Card list on phones, table on md+ */}
          <div className="divide-y divide-slate-800/60 md:hidden">
            {entries.map((entry, i) => {
              const isMe = entry.user_id === user?.id
              return (
                <div key={entry.user_id} className={`px-4 py-3 ${isMe ? 'bg-amber-500/10' : ''}`}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="w-7 shrink-0 text-slate-400">
                        {i < medals.length ? medals[i] : i + 1}
                      </span>
                      <span className={`truncate font-medium ${isMe ? 'text-amber-400' : ''}`}>
                        {entry.display_name}
                        {isMe && <span className="ml-2 text-xs text-slate-500">{t('leaderboard.you')}</span>}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono font-semibold text-white">{fmtEur(entry.total_eur)}</span>
                  </div>
                  <p className="mt-1 pl-9 text-xs text-slate-500">
                    {t('leaderboard.cash')}: <span className="font-mono text-slate-400">{fmtEur(entry.cash_eur)}</span>
                    <span className="mx-1.5">·</span>
                    {t('leaderboard.assets')}:{' '}
                    <span className="font-mono text-slate-400">{fmtEur(entry.assets_eur)}</span>
                    <span className="mx-1.5">·</span>
                    {t('leaderboard.trades')}: <span className="font-mono text-slate-400">{entry.trades}</span>
                  </p>
                </div>
              )
            })}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-3 w-12">#</th>
                  <th className="px-4 py-3">{t('leaderboard.user')}</th>
                  <th className="px-4 py-3 text-right">{t('leaderboard.trades')}</th>
                  <th className="px-4 py-3 text-right">{t('leaderboard.cash')}</th>
                  <th className="px-4 py-3 text-right">{t('leaderboard.assets')}</th>
                  <th className="px-4 py-3 text-right">{t('leaderboard.total')}</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry, i) => {
                  const isMe = entry.user_id === user?.id
                  return (
                    <tr
                      key={entry.user_id}
                      className={`border-t border-slate-800/60 ${
                        isMe ? 'bg-amber-500/10' : 'hover:bg-slate-800/30'
                      }`}
                    >
                      <td className="px-4 py-2.5 text-slate-400">
                        {i < medals.length ? medals[i] : i + 1}
                      </td>
                      <td className={`px-4 py-2.5 font-medium ${isMe ? 'text-amber-400' : ''}`}>
                        {entry.display_name}
                        {isMe && (
                          <span className="ml-2 text-xs text-slate-500">{t('leaderboard.you')}</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-slate-300">{entry.trades}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-slate-300">{fmtEur(entry.cash_eur)}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-slate-300">{fmtEur(entry.assets_eur)}</td>
                      <td className="px-4 py-2.5 text-right font-mono font-semibold text-white">
                        {fmtEur(entry.total_eur)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          </>
        )}
        <p className="border-t border-slate-800 px-4 py-2 text-xs text-slate-500">{t('leaderboard.note')}</p>
      </div>
    </div>
  )
}
