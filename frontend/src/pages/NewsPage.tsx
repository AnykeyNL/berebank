import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { fmtDateTime, stripHtml } from '../lib/format'
import type { NewsItem, NewsPage as NewsPageData } from '../lib/types'

const PAGE_SIZE = 20
const MAX_PAGES = 5

function pageButtonClass(active: boolean, disabled: boolean): string {
  const base = 'min-w-9 rounded-md px-3 py-1.5 text-sm font-medium transition-colors'
  if (disabled) return `${base} cursor-not-allowed text-slate-600`
  if (active) return `${base} bg-amber-500/15 text-amber-400`
  return `${base} text-slate-300 hover:bg-slate-800 hover:text-white`
}

export default function NewsPage() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const page = Math.min(Math.max(parseInt(searchParams.get('page') ?? '1', 10) || 1, 1), MAX_PAGES)
  const [data, setData] = useState<NewsPageData | null>(null)
  const [error, setError] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(false)
    setExpandedId(null)

    api<NewsPageData>(`/news?page=${page}&page_size=${PAGE_SIZE}`)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })

    return () => {
      cancelled = true
    }
  }, [page])

  function goToPage(nextPage: number) {
    setSearchParams(nextPage <= 1 ? {} : { page: String(nextPage) })
  }

  const totalPages = data?.total_pages ?? 1
  const currentPage = data?.page ?? page

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">{t('news.pageTitle')}</h1>
        <p className="mt-1 text-sm text-slate-500">{t('news.source')}</p>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        {error && (
          <p className="py-8 text-center text-sm text-slate-500">{t('news.loadError')}</p>
        )}

        {!error && data === null && (
          <div className="space-y-3">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="animate-pulse rounded-md bg-slate-800/40 p-4">
                <div className="mb-2 h-3 w-24 rounded bg-slate-700" />
                <div className="mb-1 h-4 w-full rounded bg-slate-700" />
                <div className="h-4 w-2/3 rounded bg-slate-700" />
              </div>
            ))}
          </div>
        )}

        {!error && data !== null && data.items.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-500">{t('news.emptyGlobal')}</p>
        )}

        {!error && data !== null && data.items.length > 0 && (
          <ul className="space-y-3">
            {data.items.map((item) => (
              <NewsArticle
                key={item.id}
                item={item}
                expanded={expandedId === item.id}
                onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
              />
            ))}
          </ul>
        )}

        {!error && data !== null && data.total_count > 0 && totalPages > 1 && (
          <nav
            className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 pt-4"
            aria-label={t('news.pagination')}
          >
            <button
              type="button"
              className={pageButtonClass(false, currentPage <= 1)}
              disabled={currentPage <= 1}
              onClick={() => goToPage(currentPage - 1)}
            >
              {t('news.prevPage')}
            </button>

            <div className="flex items-center gap-1">
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <button
                  key={p}
                  type="button"
                  className={pageButtonClass(p === currentPage, false)}
                  onClick={() => goToPage(p)}
                  aria-current={p === currentPage ? 'page' : undefined}
                >
                  {p}
                </button>
              ))}
            </div>

            <button
              type="button"
              className={pageButtonClass(false, currentPage >= totalPages)}
              disabled={currentPage >= totalPages}
              onClick={() => goToPage(currentPage + 1)}
            >
              {t('news.nextPage')}
            </button>
          </nav>
        )}
      </div>
    </div>
  )
}

function NewsArticle({
  item,
  expanded,
  onToggle,
}: {
  item: NewsItem
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const preview = stripHtml(item.body)

  return (
    <li className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <p className="text-xs text-slate-500">{fmtDateTime(item.datetime)}</p>
      <button type="button" className="mt-1 w-full text-left" onClick={onToggle}>
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-slate-100 hover:text-amber-400"
            onClick={(e) => e.stopPropagation()}
          >
            {item.title}
          </a>
        ) : (
          <p className="font-medium text-slate-100">{item.title}</p>
        )}
        <p className={`mt-1 text-sm text-slate-400 ${expanded ? '' : 'line-clamp-2'}`}>
          {preview}
        </p>
        {item.source && (
          <p className="mt-1 text-xs text-slate-500">{item.source}</p>
        )}
        {preview.length > 120 && (
          <span className="mt-1 inline-block text-xs text-amber-400">
            {expanded ? t('news.showLess') : t('news.readMore')}
          </span>
        )}
      </button>
    </li>
  )
}
