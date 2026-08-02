import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtDateTime, fmtPct, fmtPrice } from '../lib/format'
import type {
  AnalysisRange,
  AnalysisStrategy,
  AssetClass,
  KimiAnalysis,
  KimiOutlooks,
  Market,
  Outlook,
  OutlookConfidence,
} from '../lib/types'
import { SignalBadge } from '../components/AnalysisCard'
import AnalysisCrossLinks from '../components/AnalysisCrossLinks'
import AssetClassIcon from '../components/AssetClassIcon'
import { formatReasonParams } from './AnalyzePage'

const RANGES: AnalysisRange[] = ['1d', '1w', '30d', '90d', '180d', '365d']

const DIRECTION_STYLES: Record<Outlook['direction'], string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400',
  bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-300',
  none: 'bg-slate-800 text-slate-500',
}

const CONFIDENCE_ORDER: Outlook['confidence'][] = ['low', 'medium', 'high']

function ConfidenceDots({ confidence }: { confidence: OutlookConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <span
      className="hidden items-center gap-0.5 sm:flex"
      title={`${t('kimiAnalysis.outlook.confidenceLabel')}: ${t(`kimiAnalysis.outlook.confidenceLevels.${confidence}`)}`}
    >
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`} />
      ))}
    </span>
  )
}

function ScoreBar({ score }: { score: number }) {
  const { t } = useTranslation()
  const pct = Math.min(100, Math.max(0, (score + 100) / 2))
  return (
    <div>
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span className="uppercase tracking-wide">{t('kimiAnalysis.outlook.scoreLabel')}</span>
        <span className="font-mono text-sm text-slate-200">{score > 0 ? `+${score}` : score}</span>
      </div>
      <div className="relative mt-1 h-2 rounded-full bg-gradient-to-r from-red-500/60 via-slate-600/60 to-emerald-500/60">
        <div
          className="absolute top-1/2 h-4 w-1 -translate-y-1/2 rounded-full bg-white shadow"
          style={{ left: `calc(${pct}% - 2px)` }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{t('kimiAnalysis.outlook.scoreMin')}</span>
        <span>{t('kimiAnalysis.outlook.scoreMax')}</span>
      </div>
    </div>
  )
}

function ConfidenceMeter({ confidence }: { confidence: Outlook['confidence'] }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">
        {t('kimiAnalysis.outlook.confidenceLabel')}
      </p>
      <div className="mt-1 flex items-center gap-1">
        {CONFIDENCE_ORDER.map((level, i) => (
          <span
            key={level}
            className={`h-2 w-8 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`}
          />
        ))}
        <span className="ml-2 text-sm font-medium text-slate-200">
          {t(`kimiAnalysis.outlook.confidenceLevels.${confidence}`)}
        </span>
      </div>
    </div>
  )
}

type SortKey = 'asset' | 'confidence' | 'score' | 'direction' | 'last'
type SortDir = 'asc' | 'desc'

const DIRECTION_RANK: Record<string, number> = { bullish: 3, neutral: 2, bearish: 1, none: 0 }
const CONFIDENCE_RANK: Record<OutlookConfidence, number> = { high: 3, medium: 2, low: 1 }

function AssetPicker() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { prices } = usePrices()
  const [markets, setMarkets] = useState<Market[]>([])
  const [outlooks, setOutlooks] = useState<KimiOutlooks['outlooks']>({})
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<'all' | AssetClass>('all')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'score', dir: 'desc' })

  useEffect(() => {
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
    api<KimiOutlooks>('/markets/kimi-outlooks')
      .then((data) => setOutlooks(data.outlooks))
      .catch(() => {})
  }, [])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return markets
      .filter((m) => classFilter === 'all' || m.asset_class === classFilter)
      .filter((m) => {
        if (!q) return true
        return `${m.market} ${m.base} ${m.name ?? ''} ${m.listing ?? ''}`.toLowerCase().includes(q)
      })
  }, [markets, search, classFilter])

  const sortedRows = useMemo(() => {
    const value = (m: Market): number | string | null => {
      const outlook = outlooks[m.market]
      switch (sort.key) {
        case 'asset':
          return m.base
        case 'confidence':
          return outlook ? CONFIDENCE_RANK[outlook.confidence] : null
        case 'score':
          return outlook ? outlook.score : null
        case 'direction':
          return outlook ? DIRECTION_RANK[outlook.direction] : null
        case 'last': {
          const last = prices[m.market]?.last ?? m.last
          return last !== null ? parseFloat(last) : null
        }
      }
    }
    const mult = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      // Rows without a value (no outlook yet / no price) always sort last.
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
    <div className="rounded-xl border border-slate-800 bg-slate-900/60">
      <div className="border-b border-slate-800 p-3">
        <h2 className="px-1 pb-2 text-lg font-bold">{t('kimiAnalysis.pageTitle')}</h2>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('trade.searchPlaceholder')}
          aria-label={t('kimiAnalysis.pickAsset')}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm outline-none focus:border-amber-500"
        />
        <div className="mt-2 flex gap-1">
          {(['all', 'crypto', 'stock', 'fund', 'commodity'] as const).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setClassFilter(c)}
              title={t(`trade.filter.${c}`)}
              aria-label={t(`trade.filter.${c}`)}
              aria-pressed={classFilter === c}
              className={`flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors ${
                classFilter === c
                  ? 'bg-amber-500/15 text-amber-400'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {c === 'all' ? (
                <span className="text-xs font-medium">{t('trade.filter.all')}</span>
              ) : (
                <AssetClassIcon assetClass={c} className="h-5 w-5" />
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="max-h-[60vh] overflow-y-auto">
        <div className="flex items-center gap-2 border-b border-slate-800/60 px-4 py-1.5 text-[10px] text-slate-500">
          {sortHeader('asset', t('kimiAnalysis.table.asset'), 'flex-1')}
          {sortHeader(
            'confidence',
            <span className="flex items-center gap-0.5" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 rounded-full bg-current" />
              ))}
            </span>,
            'hidden w-12 justify-center sm:flex',
            t('kimiAnalysis.outlook.confidenceLabel'),
          )}
          {sortHeader('score', t('kimiAnalysis.outlook.scoreLabel'), 'w-12 justify-end sm:w-14')}
          {sortHeader('direction', t('kimiAnalysis.table.outlook'), 'w-20 justify-center sm:w-24')}
          {sortHeader('last', t('trade.last'), 'w-16 justify-end sm:w-20')}
        </div>
        {sortedRows.map((m) => {
          const last = prices[m.market]?.last ?? m.last
          const outlook = outlooks[m.market]
          return (
            <button
              key={m.market}
              type="button"
              onClick={() => navigate(`/kimi-analysis/${m.market}`)}
              className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-slate-800/50"
            >
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 font-medium">
                  <AssetClassIcon assetClass={m.asset_class} className="h-3.5 w-3.5 shrink-0" />
                  {m.base}
                </span>
                {m.name && <span className="block truncate text-xs text-slate-500">{m.name}</span>}
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
                    title={t('kimiAnalysis.trackRecord.noHistory')}
                  >
                    {t('kimiAnalysis.table.collecting')}
                  </span>
                )}
              </span>
              <span className="w-16 text-right font-mono text-xs sm:w-20">{fmtPrice(last)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function KimiAnalysisPage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const { prices } = usePrices()

  const market = (marketParam ?? '').toUpperCase()
  const [range, setRange] = useState<AnalysisRange>('30d')
  const [analysis, setAnalysis] = useState<KimiAnalysis | null>(null)
  const [error, setError] = useState(false)
  const [markets, setMarkets] = useState<Market[]>([])
  const [showWhy, setShowWhy] = useState(false)

  useEffect(() => {
    if (market) api<Market[]>('/markets').then(setMarkets).catch(() => {})
  }, [market])

  useEffect(() => {
    if (!market) return
    let cancelled = false
    setAnalysis(null)
    setError(false)

    function load() {
      api<KimiAnalysis>(`/markets/${encodeURIComponent(market)}/kimi-analysis?range=${range}`)
        .then((data) => {
          if (!cancelled) {
            setAnalysis(data)
            setError(false)
          }
        })
        .catch(() => {
          if (!cancelled) setError(true)
        })
    }

    load()
    const timer = setInterval(load, 60000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [market, range])

  const marketInfo = markets.find((m) => m.market === market)
  const livePrice = prices[market]?.last ?? marketInfo?.last ?? null

  const changePct = useMemo(() => {
    if (!analysis || analysis.candles.length < 2) return null
    const first = parseFloat(analysis.candles[0][4])
    const last = parseFloat(analysis.candles[analysis.candles.length - 1][4])
    return first !== 0 ? ((last - first) / first) * 100 : null
  }, [analysis])

  if (!market) return <AssetPicker />

  function contributionReason(key: string, strategy: AnalysisStrategy): string {
    if (key === 'trend_strength') {
      const adx = strategy.reason.params.adx
      return t(`kimiAnalysis.reasons.${strategy.reason.code}`, {
        adx: adx != null ? parseFloat(String(adx)).toFixed(1) : '—',
      })
    }
    return t(`analyze.reasons.${strategy.reason.code}`, formatReasonParams(strategy.reason.params, t))
  }

  const outlook = analysis?.outlook ?? null
  const trackRecord = analysis?.track_record ?? null

  const btnBase =
    'rounded px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500 md:px-2 md:py-1'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3 text-xs">
              <Link to={`/trade/${market}`} className="text-amber-400 hover:text-amber-300">
                ← {t('kimiAnalysis.backToTrade')}
              </Link>
              <Link to="/kimi-analysis" className="text-slate-400 hover:text-slate-200">
                {t('kimiAnalysis.changeAsset')}
              </Link>
              <AnalysisCrossLinks market={market} current="kimi" />
            </div>
            <h2 className="mt-1 flex items-center gap-2 text-xl font-bold">
              {marketInfo && <AssetClassIcon assetClass={marketInfo.asset_class} className="h-5 w-5" />}
              {t('kimiAnalysis.pageTitle')}: {market}
            </h2>
            {marketInfo?.name && <p className="text-sm text-slate-300">{marketInfo.name}</p>}
          </div>
          <div className="text-right">
            <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.last')}</p>
            <p className="font-mono text-lg">{fmtPrice(livePrice)}</p>
            {changePct !== null && (
              <p className={`text-sm font-medium ${changePct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {fmtPct(changePct)} · {t(`chart.ranges.${range}`)}
              </p>
            )}
          </div>
        </div>
        <div
          className="mt-3 flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5 sm:w-fit"
          role="group"
          aria-label={t('kimiAnalysis.range')}
        >
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              className={`${btnBase} ${range === r ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
              onClick={() => setRange(r)}
              aria-pressed={range === r}
            >
              {t(`chart.ranges.${r}`)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-500">
          {t('kimiAnalysis.loadError')}
        </div>
      )}
      {!error && !analysis && <div className="h-64 animate-pulse rounded-xl bg-slate-800/40" />}

      {analysis && outlook && (
        <>
          {/* Outlook hero */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">
                  {t('kimiAnalysis.outlook.title')}
                </p>
                <span
                  className={`mt-1 inline-block rounded-md px-3 py-1 text-lg font-bold uppercase tracking-wide ${DIRECTION_STYLES[outlook.direction]}`}
                >
                  {t(`analyze.signals.${outlook.direction}`)}
                </span>
                <p className="mt-2 max-w-md text-sm text-slate-300">
                  {t(`kimiAnalysis.outlook.reasons.${outlook.reason.code}`, outlook.reason.params)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t(`kimiAnalysis.outlook.regimeNote.${outlook.regime}`)}
                </p>
              </div>
              <div className="w-full max-w-xs space-y-4">
                <ScoreBar score={outlook.score} />
                <ConfidenceMeter confidence={outlook.confidence} />
              </div>
            </div>

            <button
              type="button"
              onClick={() => setShowWhy((v) => !v)}
              className="mt-4 text-xs font-medium text-amber-400 hover:text-amber-300"
              aria-expanded={showWhy}
            >
              {showWhy ? t('kimiAnalysis.hideWhy') : t('kimiAnalysis.why')}
            </button>
            {showWhy && (
              <ul className="mt-3 divide-y divide-slate-800/60 border-t border-slate-800">
                {outlook.contributions.map((c) => {
                  const strategy = analysis.strategies[c.strategy as keyof KimiAnalysis['strategies']]
                  return (
                    <li key={c.strategy} className="flex flex-wrap items-center gap-2 py-2.5">
                      <span className="w-48 text-sm font-medium">
                        {t(`kimiAnalysis.strategyNames.${c.strategy}`)}
                      </span>
                      <SignalBadge signal={c.signal} />
                      {c.weight > 1 && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
                          {t('kimiAnalysis.weight', { weight: c.weight })}
                        </span>
                      )}
                      {strategy && (
                        <span className="w-full text-xs text-slate-400 sm:ml-auto sm:w-auto sm:max-w-md sm:text-right">
                          {contributionReason(c.strategy, strategy)}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            <p className="mt-3 text-xs text-slate-500">
              {t('kimiAnalysis.updated', { time: fmtDateTime(analysis.generated_at) })}
            </p>
          </div>

          {/* Track record */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="font-semibold">{t('kimiAnalysis.trackRecord.title', { market })}</h3>
            {trackRecord ? (
              <>
                <p className="mt-2 text-sm text-slate-300">
                  {t('kimiAnalysis.trackRecord.summary', {
                    hitRate: trackRecord.hit_rate_pct,
                    days: trackRecord.forward_days,
                  })}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t('kimiAnalysis.trackRecord.samples', {
                    samples: trackRecord.samples,
                    from: trackRecord.from,
                    to: trackRecord.to,
                  })}
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {trackRecord.avg_bullish_return_pct !== null && (
                    <span className="rounded bg-emerald-500/10 px-2 py-1 font-mono text-emerald-300">
                      {t('kimiAnalysis.trackRecord.avgBullish', { value: trackRecord.avg_bullish_return_pct })}
                    </span>
                  )}
                  {trackRecord.avg_bearish_return_pct !== null && (
                    <span className="rounded bg-red-500/10 px-2 py-1 font-mono text-red-300">
                      {t('kimiAnalysis.trackRecord.avgBearish', { value: trackRecord.avg_bearish_return_pct })}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="mt-2 text-sm text-slate-500">{t('kimiAnalysis.trackRecord.noHistory')}</p>
            )}
          </div>
        </>
      )}

      <p className="text-xs text-slate-500">{t('kimiAnalysis.disclaimer')}</p>
    </div>
  )
}
