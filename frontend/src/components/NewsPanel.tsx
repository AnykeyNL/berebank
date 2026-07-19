import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { fmtDateTime } from '../lib/format'
import type { NewsItem } from '../lib/types'

function stripHtml(html: string): string {
  return html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
}

export default function NewsPanel({ market }: { market: string }) {
  const { t } = useTranslation()
  const [items, setItems] = useState<NewsItem[] | null>(null)
  const [error, setError] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setItems(null)
    setError(false)
    setExpandedId(null)

    function load() {
      api<NewsItem[]>(`/markets/${encodeURIComponent(market)}/news`)
        .then((data) => {
          if (!cancelled) {
            setItems(data)
            setError(false)
          }
        })
        .catch(() => {
          if (!cancelled) setError(true)
        })
    }

    load()
    const timer = setInterval(load, 300000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [market])

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="mb-3 font-semibold">{t('news.title')}</h3>

      {error && (
        <p className="py-8 text-center text-sm text-slate-500">{t('news.loadError')}</p>
      )}

      {!error && items === null && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="animate-pulse rounded-md bg-slate-800/40 p-4">
              <div className="mb-2 h-3 w-24 rounded bg-slate-700" />
              <div className="mb-1 h-4 w-full rounded bg-slate-700" />
              <div className="h-4 w-2/3 rounded bg-slate-700" />
            </div>
          ))}
        </div>
      )}

      {!error && items !== null && items.length === 0 && (
        <p className="py-8 text-center text-sm text-slate-500">{t('news.empty')}</p>
      )}

      {!error && items !== null && items.length > 0 && (
        <ul className="max-h-[420px] space-y-3 overflow-y-auto">
          {items.map((item) => {
            const preview = stripHtml(item.body)
            const expanded = expandedId === item.id
            return (
              <li
                key={item.id}
                className="rounded-lg border border-slate-800 bg-slate-950/40 p-3"
              >
                <p className="text-xs text-slate-500">{fmtDateTime(item.datetime)}</p>
                <button
                  type="button"
                  className="mt-1 w-full text-left"
                  onClick={() => setExpandedId(expanded ? null : item.id)}
                >
                  <p className="font-medium text-slate-100">{item.title}</p>
                  <p className={`mt-1 text-sm text-slate-400 ${expanded ? '' : 'line-clamp-2'}`}>
                    {preview}
                  </p>
                  {preview.length > 120 && (
                    <span className="mt-1 inline-block text-xs text-amber-400">
                      {expanded ? t('news.showLess') : t('news.readMore')}
                    </span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <p className="mt-3 text-xs text-slate-500">{t('news.source')}</p>
    </div>
  )
}
