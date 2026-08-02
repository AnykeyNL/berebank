import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import AssetClassIcon from '../components/AssetClassIcon'
import { SignalBadge } from '../components/AnalysisCard'
import AnalysisCrossLinks from '../components/AnalysisCrossLinks'
import DirectionOutlookCard from '../components/DirectionOutlookCard'
import SupplementaryContextPanel from '../components/SupplementaryContextPanel'
import { api } from '../lib/api'
import { fmtPrice } from '../lib/format'
import type {
  AssetClass,
  GTP56SolAnalysis,
  GTP56SolConfidence,
  GTP56SolHorizon,
  GTP56SolOutlooks,
  Market,
} from '../lib/types'
import { usePrices } from '../lib/usePrices'

const HORIZONS: { horizon: GTP56SolHorizon; bars: number }[] = [
  { horizon: '1d', bars: 1 },
  { horizon: '1w', bars: 5 },
  { horizon: '1m', bars: 21 },
]

type PickerClass = 'all' | AssetClass

function pickerFilterLabel(assetClass: PickerClass, t: (key: string) => string): string {
  if (assetClass === 'crypto') return t('gtp56solAnalysis.picker.filters.crypto')
  if (assetClass === 'stock') return t('gtp56solAnalysis.picker.filters.stock')
  if (assetClass === 'fund') return t('gtp56solAnalysis.picker.filters.fund')
  if (assetClass === 'commodity') return t('gtp56solAnalysis.picker.filters.commodity')
  return t('gtp56solAnalysis.picker.filters.all')
}

function horizonLabel(horizon: GTP56SolHorizon, t: (key: string) => string): string {
  if (horizon === '1d') return t('gtp56solAnalysis.horizons.1d')
  if (horizon === '1m') return t('gtp56solAnalysis.horizons.1m')
  return t('gtp56solAnalysis.horizons.1w')
}

type HorizonState = {
  result: GTP56SolAnalysis | null
  error: boolean
}

function initialHorizonStates(): Record<GTP56SolHorizon, HorizonState> {
  return {
    '1d': { result: null, error: false },
    '1w': { result: null, error: false },
    '1m': { result: null, error: false },
  }
}

type SortKey = 'asset' | 'confidence' | 'score' | 'direction' | 'last'
type SortDir = 'asc' | 'desc'

const DIRECTION_RANK: Record<string, number> = { bullish: 3, neutral: 2, bearish: 1 }
const CONFIDENCE_RANK: Record<GTP56SolConfidence, number> = { high: 3, medium: 2, low: 1 }
const CONFIDENCE_ORDER: GTP56SolConfidence[] = ['low', 'medium', 'high']

function ConfidenceDots({ confidence }: { confidence: GTP56SolConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <span
      className="hidden items-center gap-0.5 sm:flex"
      title={`${t('gtp56solAnalysis.confidence.label', {
        level: t(`gtp56solAnalysis.confidence.${confidence}`),
      })}`}
    >
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`} />
      ))}
    </span>
  )
}

function AssetPicker() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { prices } = usePrices()
  const [markets, setMarkets] = useState<Market[]>([])
  const [outlooks, setOutlooks] = useState<GTP56SolOutlooks['outlooks']>({})
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<PickerClass>('all')
  const [loadError, setLoadError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'score', dir: 'desc' })

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(false)

    api<Market[]>('/markets')
      .then((marketData) => {
        if (!cancelled) {
          setMarkets(marketData)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadError(true)
          setLoading(false)
        }
      })

    api<GTP56SolOutlooks>('/markets/gtp56sol-outlooks?horizon=1w', {
      signal: AbortSignal.timeout(60_000),
    })
      .then((outlookData) => {
        if (!cancelled) setOutlooks(outlookData.outlooks ?? {})
      })
      .catch(() => {})

    return () => {
      cancelled = true
    }
  }, [])

  const rows = useMemo(() => {
    const query = search.trim().toLowerCase()
    return markets.filter((market) => {
      if (classFilter !== 'all' && market.asset_class !== classFilter) return false
      if (!query) return true
      return `${market.market} ${market.base} ${market.name ?? ''} ${market.listing ?? ''}`
        .toLowerCase()
        .includes(query)
    })
  }, [classFilter, markets, search])

  const sortedRows = useMemo(() => {
    const value = (market: Market): number | string | null => {
      const outlook = outlooks[market.market]
      switch (sort.key) {
        case 'asset':
          return market.base
        case 'confidence':
          return outlook ? CONFIDENCE_RANK[outlook.confidence] : null
        case 'score':
          return outlook ? outlook.score : null
        case 'direction':
          return outlook ? DIRECTION_RANK[outlook.direction] : null
        case 'last': {
          const last = prices[market.market]?.last ?? market.last
          return last !== null ? parseFloat(last) : null
        }
      }
    }
    const mult = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      if (va === null && vb === null) return 0
      if (va === null) return 1
      if (vb === null) return -1
      const cmp = typeof va === 'string' ? va.localeCompare(vb as string) : va - (vb as number)
      return cmp * mult
    })
  }, [rows, outlooks, prices, sort])

  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'asset' ? 'asc' : 'desc' },
    )
  }

  function sortHeader(key: SortKey, label: ReactNode, className: string, title?: string) {
    const active = sort.key === key
    return (
      <button
        type="button"
        onClick={() => toggleSort(key)}
        aria-pressed={active}
        title={title}
        className={`flex items-center gap-0.5 uppercase tracking-wide transition-colors hover:text-slate-300 ${
          active ? 'text-slate-300' : ''
        } ${className}`}
      >
        <span className="truncate">{label}</span>
        {active && <span aria-hidden="true">{sort.dir === 'asc' ? '↑' : '↓'}</span>}
      </button>
    )
  }

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60">
      <header className="border-b border-slate-800 p-4">
        <h1 className="text-xl font-bold">{t('gtp56solAnalysis.brand')}</h1>
        <p className="mt-1 text-sm text-slate-400">{t('gtp56solAnalysis.picker.intro')}</p>
        <label className="mt-4 block">
          <span className="sr-only">{t('gtp56solAnalysis.picker.searchLabel')}</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('gtp56solAnalysis.picker.searchPlaceholder')}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-500 focus-visible:ring-2 focus-visible:ring-amber-400"
          />
        </label>
        <div
          className="mt-2 flex gap-1"
          role="group"
          aria-label={t('gtp56solAnalysis.picker.filterLabel')}
        >
          {(['all', 'crypto', 'stock', 'fund', 'commodity'] as const).map((assetClass) => (
            <button
              key={assetClass}
              type="button"
              onClick={() => setClassFilter(assetClass)}
              aria-label={pickerFilterLabel(assetClass, t)}
              aria-pressed={classFilter === assetClass}
              className={`flex flex-1 items-center justify-center rounded-md py-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                classFilter === assetClass
                  ? 'bg-amber-500/15 text-amber-400'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {assetClass === 'all' ? (
                <span className="text-xs font-medium">
                  {t('gtp56solAnalysis.picker.filters.all')}
                </span>
              ) : (
                <AssetClassIcon assetClass={assetClass} className="h-5 w-5" />
              )}
            </button>
          ))}
        </div>
      </header>

      {loading ? (
        <p role="status" className="p-6 text-center text-sm text-slate-400">
          {t('gtp56solAnalysis.picker.loading')}
        </p>
      ) : loadError ? (
        <p role="alert" className="p-6 text-center text-sm text-red-300">
          {t('gtp56solAnalysis.picker.loadError')}
        </p>
      ) : (
        <div className="max-h-[65vh] overflow-y-auto">
          <div className="flex items-center gap-2 border-b border-slate-800/60 px-4 py-1.5 text-[10px] text-slate-500">
            {sortHeader('asset', t('gtp56solAnalysis.table.asset'), 'flex-1')}
            {sortHeader(
              'confidence',
              <span className="flex items-center gap-0.5" aria-hidden="true">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="h-1.5 w-1.5 rounded-full bg-current" />
                ))}
              </span>,
              'hidden w-12 justify-center sm:flex',
              t('gtp56solAnalysis.table.confidence'),
            )}
            {sortHeader('score', t('gtp56solAnalysis.table.score'), 'w-12 justify-end sm:w-14')}
            {sortHeader('direction', t('gtp56solAnalysis.table.outlook'), 'w-20 justify-center sm:w-24')}
            {sortHeader('last', t('trade.last'), 'w-16 justify-end sm:w-20')}
          </div>
          <div className="divide-y divide-slate-800/60">
            {sortedRows.map((market) => {
              const price = prices[market.market]?.last ?? market.last
              const outlook = outlooks[market.market]
              return (
                <button
                  key={market.market}
                  type="button"
                  onClick={() => navigate(`/gtp56sol-analysis/${market.market}`)}
                  aria-label={`${market.base} ${market.name ?? market.market}`}
                  className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-slate-800/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-400"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 font-medium">
                      <AssetClassIcon assetClass={market.asset_class} className="h-3.5 w-3.5 shrink-0" />
                      {market.base}
                    </span>
                    {market.name && (
                      <span className="block truncate text-xs text-slate-500">{market.name}</span>
                    )}
                  </span>
                  <span className="hidden w-12 justify-center sm:flex">
                    {outlook && <ConfidenceDots confidence={outlook.confidence} />}
                  </span>
                  <span
                    className={`w-12 text-right font-mono text-xs sm:w-14 ${
                      !outlook
                        ? ''
                        : outlook.score > 0
                          ? 'text-emerald-400'
                          : outlook.score < 0
                            ? 'text-red-400'
                            : 'text-slate-400'
                    }`}
                  >
                    {outlook ? (outlook.score > 0 ? `+${outlook.score}` : outlook.score) : ''}
                  </span>
                  <span className="flex w-20 justify-center sm:w-24">
                    {outlook ? (
                      <SignalBadge signal={outlook.direction} />
                    ) : (
                      <span
                        className="truncate text-[10px] uppercase tracking-wide text-slate-600"
                        title={t('gtp56solAnalysis.table.collectingHint')}
                      >
                        {t('gtp56solAnalysis.table.collecting')}
                      </span>
                    )}
                  </span>
                  <span className="w-16 text-right font-mono text-xs sm:w-20">{fmtPrice(price)}</span>
                </button>
              )
            })}
          </div>
          {sortedRows.length === 0 && (
            <p className="p-6 text-center text-sm text-slate-500">
              {markets.length === 0
                ? t('gtp56solAnalysis.picker.emptyAvailable')
                : t('gtp56solAnalysis.picker.noMatches')}
            </p>
          )}
        </div>
      )}
      <p className="border-t border-slate-800 p-4 text-xs text-slate-500">
        {t('gtp56solAnalysis.disclaimer')}
      </p>
    </section>
  )
}

export default function GTP56SolAnalysisPage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const { prices } = usePrices()
  const market = (marketParam ?? '').toUpperCase()
  const currentMarket = useRef(market)
  const mounted = useRef(false)
  currentMarket.current = market
  const [markets, setMarkets] = useState<Market[]>([])
  const [states, setStates] = useState(initialHorizonStates)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const loadHorizon = useCallback((horizon: GTP56SolHorizon) => {
    const requestedMarket = market
    if (!requestedMarket) return
    api<GTP56SolAnalysis>(
      `/markets/${encodeURIComponent(requestedMarket)}/gtp56sol-analysis?horizon=${horizon}`,
      { signal: AbortSignal.timeout(60_000) },
    )
      .then((result) => {
        if (mounted.current && currentMarket.current === requestedMarket) {
          setStates((current) => ({
            ...current,
            [horizon]: { result, error: false },
          }))
        }
      })
      .catch(() => {
        if (mounted.current && currentMarket.current === requestedMarket) {
          setStates((current) => ({
            ...current,
            [horizon]: { result: current[horizon].result, error: true },
          }))
        }
      })
  }, [market])

  useEffect(() => {
    if (!market) return
    let cancelled = false
    api<Market[]>('/markets')
      .then((data) => {
        if (!cancelled) setMarkets(data)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [market])

  useEffect(() => {
    if (!market) return
    function load() {
      for (const { horizon } of HORIZONS) {
        loadHorizon(horizon)
      }
    }

    setStates(initialHorizonStates())
    load()
    const timer = window.setInterval(load, 5 * 60 * 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [loadHorizon, market])

  if (!market) return <AssetPicker />

  const marketInfo = markets.find((item) => item.market === market)
  const livePrice = prices[market]?.last ?? marketInfo?.last ?? null

  return (
    <div className="space-y-6">
      <header className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
          <Link to={`/trade/${market}`} className="text-amber-400 hover:text-amber-300 focus-visible:outline-amber-400">
            ← {t('gtp56solAnalysis.links.trade')}
          </Link>
          <Link to="/gtp56sol-analysis" className="text-slate-400 hover:text-slate-200">
            {t('gtp56solAnalysis.links.changeAsset')}
          </Link>
          <AnalysisCrossLinks market={market} current="gtp56sol" />
        </div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold">
              {marketInfo && (
                <AssetClassIcon assetClass={marketInfo.asset_class} className="h-5 w-5" />
              )}
              {t('gtp56solAnalysis.brand')}: {market}
            </h1>
            {marketInfo?.name && <p className="text-sm text-slate-300">{marketInfo.name}</p>}
          </div>
          <div className="text-right">
            <p className="text-xs uppercase tracking-wide text-slate-500">
              {t('gtp56solAnalysis.lastPrice')}
            </p>
            <p className="font-mono text-lg">{fmtPrice(livePrice)}</p>
          </div>
        </div>
        <p className="mt-3 text-xs text-slate-500">{t('gtp56solAnalysis.refreshNote')}</p>
      </header>

      <section className="grid gap-4 lg:grid-cols-3" aria-label={t('gtp56solAnalysis.horizons.label')}>
        {HORIZONS.map(({ horizon, bars }) => (
          <DirectionOutlookCard
            key={horizon}
            title={horizonLabel(horizon, t)}
            bars={bars}
            result={states[horizon].result}
            error={states[horizon].error}
            onRetry={() => loadHorizon(horizon)}
          />
        ))}
      </section>

      <SupplementaryContextPanel
        context={states['1w'].result?.context}
        namespace="gtp56solAnalysis"
      />

      <p className="text-xs text-slate-500">{t('gtp56solAnalysis.disclaimer')}</p>
    </div>
  )
}
