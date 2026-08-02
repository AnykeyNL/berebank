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
  Fable5Analysis,
  Fable5Outlooks,
  Market,
  Outlook,
  OutlookConfidence,
} from '../lib/types'
import { SignalBadge } from '../components/AnalysisCard'
import AnalysisCrossLinks from '../components/AnalysisCrossLinks'
import AssetClassIcon from '../components/AssetClassIcon'
import SupplementaryContextPanel from '../components/SupplementaryContextPanel'
import { formatReasonParams } from './AnalyzePage'

const RANGES: AnalysisRange[] = ['1d', '1w', '30d', '90d', '180d', '365d']

const DIRECTION_STYLES: Record<Outlook['direction'], string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400',
  bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-300',
  none: 'bg-slate-800 text-slate-500',
}

const CONFIDENCE_ORDER: Outlook['confidence'][] = ['low', 'medium', 'high']

// Strategies whose reason codes live in the fable5Analysis i18n namespace.
const FABLE5_STRATEGIES = new Set([
  'momentum',
  'stochastic',
  'trend_strength',
  'vix_regime',
  'yield_curve',
  'funding_regime',
  'oi_momentum',
  'long_short',
  'liquidations',
  'relative_strength',
  'event_risk',
])

type GaugeZone = 'strong_down' | 'down' | 'neutral' | 'up' | 'strong_up'

const GAUGE_ZONES: { id: GaugeZone; from: number; to: number; color: string }[] = [
  { id: 'strong_down', from: -100, to: -60, color: '#ef4444' },
  { id: 'down', from: -60, to: -20, color: '#fb923c' },
  { id: 'neutral', from: -20, to: 20, color: '#64748b' },
  { id: 'up', from: 20, to: 60, color: '#a3e635' },
  { id: 'strong_up', from: 60, to: 100, color: '#34d399' },
]

function zoneFor(score: number): GaugeZone {
  if (score >= 60) return 'strong_up'
  if (score >= 20) return 'up'
  if (score > -20) return 'neutral'
  if (score > -60) return 'down'
  return 'strong_down'
}

// Map a -100..+100 score onto the gauge semicircle (center 100,100).
function gaugePoint(radius: number, score: number): [number, number] {
  const deg = 180 - ((score + 100) / 200) * 180
  const rad = (deg * Math.PI) / 180
  return [100 + radius * Math.cos(rad), 100 - radius * Math.sin(rad)]
}

function DirectionGauge({ score }: { score: number }) {
  const { t } = useTranslation()
  const clamped = Math.max(-100, Math.min(100, score))
  const zone = zoneFor(clamped)
  const zoneColor = GAUGE_ZONES.find((z) => z.id === zone)?.color
  const [nx, ny] = gaugePoint(58, clamped)
  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox="0 0 200 108"
        className="w-full max-w-[260px]"
        role="img"
        aria-label={`${t('fable5Analysis.gauge.label')}: ${t(`fable5Analysis.gauge.zones.${zone}`)}`}
      >
        {GAUGE_ZONES.map((z) => {
          const [x1, y1] = gaugePoint(80, z.from + 1.5)
          const [x2, y2] = gaugePoint(80, z.to - 1.5)
          return (
            <path
              key={z.id}
              d={`M ${x1} ${y1} A 80 80 0 0 1 ${x2} ${y2}`}
              fill="none"
              stroke={z.color}
              strokeWidth={13}
              strokeLinecap="round"
              opacity={zone === z.id ? 1 : 0.3}
            />
          )
        })}
        <line x1={100} y1={100} x2={nx} y2={ny} stroke="#e2e8f0" strokeWidth={3} strokeLinecap="round" />
        <circle cx={100} cy={100} r={5.5} fill="#e2e8f0" />
      </svg>
      <div className="-mt-3 text-center">
        <span className="font-mono text-2xl text-slate-100">{clamped > 0 ? `+${clamped}` : clamped}</span>
        <p className="text-sm font-semibold" style={{ color: zoneColor }}>
          {t(`fable5Analysis.gauge.zones.${zone}`)}
        </p>
      </div>
      <div className="mt-1 flex w-full max-w-[260px] justify-between text-[10px] text-slate-500">
        <span>{t('fable5Analysis.outlook.scoreMin')}</span>
        <span>{t('fable5Analysis.outlook.scoreMax')}</span>
      </div>
    </div>
  )
}

function ConfidenceDots({ confidence }: { confidence: OutlookConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <span
      className="hidden items-center gap-0.5 sm:flex"
      title={`${t('fable5Analysis.outlook.confidenceLabel')}: ${t(`fable5Analysis.outlook.confidenceLevels.${confidence}`)}`}
    >
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`} />
      ))}
    </span>
  )
}

function ConfidenceMeter({ confidence }: { confidence: Outlook['confidence'] }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">
        {t('fable5Analysis.outlook.confidenceLabel')}
      </p>
      <div className="mt-1 flex items-center gap-1">
        {CONFIDENCE_ORDER.map((level, i) => (
          <span
            key={level}
            className={`h-2 w-8 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`}
          />
        ))}
        <span className="ml-2 text-sm font-medium text-slate-200">
          {t(`fable5Analysis.outlook.confidenceLevels.${confidence}`)}
        </span>
      </div>
    </div>
  )
}

type SortKey = 'asset' | 'confidence' | 'score' | 'buy' | 'sell' | 'direction' | 'last'
type SortDir = 'asc' | 'desc'

const DIRECTION_RANK: Record<string, number> = { bullish: 3, neutral: 2, bearish: 1, none: 0 }
const CONFIDENCE_RANK: Record<OutlookConfidence, number> = { high: 3, medium: 2, low: 1 }

function AssetPicker() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { prices } = usePrices()
  const [markets, setMarkets] = useState<Market[]>([])
  const [outlooks, setOutlooks] = useState<Fable5Outlooks['outlooks']>({})
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<'all' | AssetClass>('all')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'buy', dir: 'desc' })

  useEffect(() => {
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
    api<Fable5Outlooks>('/markets/fable5-outlooks')
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
        case 'buy':
          return outlook?.buy_score ?? null
        case 'sell':
          return outlook?.sell_score ?? null
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
        <h2 className="px-1 pb-2 text-lg font-bold">{t('fable5Analysis.pageTitle')}</h2>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('trade.searchPlaceholder')}
          aria-label={t('fable5Analysis.pickAsset')}
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
          {sortHeader('asset', t('fable5Analysis.table.asset'), 'flex-1')}
          {sortHeader(
            'confidence',
            <span className="flex items-center gap-0.5" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 rounded-full bg-current" />
              ))}
            </span>,
            'hidden w-12 justify-center sm:flex',
            t('fable5Analysis.outlook.confidenceLabel'),
          )}
          {sortHeader(
            'score',
            t('fable5Analysis.outlook.scoreLabel'),
            'hidden w-12 justify-end sm:flex sm:w-14',
          )}
          {sortHeader(
            'buy',
            t('fable5Analysis.table.buy'),
            'w-9 justify-end sm:w-11',
            t('fable5Analysis.table.buyTitle'),
          )}
          {sortHeader(
            'sell',
            t('fable5Analysis.table.sell'),
            'w-9 justify-end sm:w-11',
            t('fable5Analysis.table.sellTitle'),
          )}
          {sortHeader('direction', t('fable5Analysis.table.outlook'), 'w-20 justify-center sm:w-24')}
          {sortHeader('last', t('trade.last'), 'w-16 justify-end sm:w-20')}
        </div>
        {sortedRows.map((m) => {
          const last = prices[m.market]?.last ?? m.last
          const outlook = outlooks[m.market]
          return (
            <button
              key={m.market}
              type="button"
              onClick={() => navigate(`/fable5-analysis/${m.market}`)}
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
                className={`hidden w-12 text-right font-mono text-xs sm:block sm:w-14 ${
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
              <span className="w-9 text-right font-mono text-xs text-emerald-400 sm:w-11">
                {outlook?.buy_score != null ? outlook.buy_score : ''}
              </span>
              <span className="w-9 text-right font-mono text-xs text-red-400 sm:w-11">
                {outlook?.sell_score != null ? outlook.sell_score : ''}
              </span>
              <span className="flex w-20 justify-center sm:w-24">
                {outlook ? (
                  <SignalBadge signal={outlook.direction} />
                ) : (
                  <span
                    className="truncate text-[10px] uppercase tracking-wide text-slate-600"
                    title={t('fable5Analysis.trackRecord.noHistory')}
                  >
                    {t('fable5Analysis.table.collecting')}
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

export default function Fable5AnalysisPage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const { prices } = usePrices()

  const market = (marketParam ?? '').toUpperCase()
  const [range, setRange] = useState<AnalysisRange>('30d')
  const [analysis, setAnalysis] = useState<Fable5Analysis | null>(null)
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
      api<Fable5Analysis>(`/markets/${encodeURIComponent(market)}/fable5-analysis?range=${range}`)
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

  function fable5Param(key: string, value: string | number | null): string {
    if (key === 'bars_ago' || key === 'days' || key === 'hours') return String(value ?? '—')
    if (key === 'etf') return String(value || '—')
    const n = parseFloat(String(value))
    if (!Number.isFinite(n)) return '—'
    if (key === 'long_usd' || key === 'short_usd') {
      if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`
      if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`
      if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`
      return `$${n.toFixed(0)}`
    }
    if (key === 'ratio') return n.toFixed(2)
    if (key.startsWith('roc')) return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`
    return n.toFixed(1)
  }

  function contributionReason(key: string, strategy: AnalysisStrategy): string {
    if (FABLE5_STRATEGIES.has(key)) {
      const params: Record<string, string> = {}
      for (const [k, v] of Object.entries(strategy.reason.params)) {
        params[k] = fable5Param(k, v)
      }
      return t(`fable5Analysis.reasons.${strategy.reason.code}`, params)
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
                ← {t('fable5Analysis.backToTrade')}
              </Link>
              <Link to="/fable5-analysis" className="text-slate-400 hover:text-slate-200">
                {t('fable5Analysis.changeAsset')}
              </Link>
              <AnalysisCrossLinks market={market} current="fable5" />
            </div>
            <h2 className="mt-1 flex items-center gap-2 text-xl font-bold">
              {marketInfo && <AssetClassIcon assetClass={marketInfo.asset_class} className="h-5 w-5" />}
              {t('fable5Analysis.pageTitle')}: {market}
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
          aria-label={t('fable5Analysis.range')}
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
          {t('fable5Analysis.loadError')}
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
                  {t('fable5Analysis.outlook.title')}
                </p>
                <span
                  className={`mt-1 inline-block rounded-md px-3 py-1 text-lg font-bold uppercase tracking-wide ${DIRECTION_STYLES[outlook.direction]}`}
                >
                  {t(`analyze.signals.${outlook.direction}`)}
                </span>
                {outlook.buy_score != null && outlook.sell_score != null && (
                  <div className="mt-2 flex gap-2 text-xs font-medium">
                    <span
                      className="rounded bg-emerald-500/10 px-2 py-0.5 text-emerald-400"
                      title={t('fable5Analysis.table.buyTitle')}
                    >
                      {t('fable5Analysis.outlook.buyScore', { value: outlook.buy_score })}
                    </span>
                    <span
                      className="rounded bg-red-500/10 px-2 py-0.5 text-red-400"
                      title={t('fable5Analysis.table.sellTitle')}
                    >
                      {t('fable5Analysis.outlook.sellScore', { value: outlook.sell_score })}
                    </span>
                  </div>
                )}
                <p className="mt-2 max-w-md text-sm text-slate-300">
                  {t(`fable5Analysis.outlook.reasons.${outlook.reason.code}`, outlook.reason.params)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t(`fable5Analysis.outlook.regimeNote.${outlook.regime}`)}
                </p>
                <div className="mt-4">
                  <ConfidenceMeter confidence={outlook.confidence} />
                </div>
              </div>
              <div className="w-full max-w-xs">
                <DirectionGauge score={outlook.score} />
              </div>
            </div>

            <button
              type="button"
              onClick={() => setShowWhy((v) => !v)}
              className="mt-4 text-xs font-medium text-amber-400 hover:text-amber-300"
              aria-expanded={showWhy}
            >
              {showWhy ? t('fable5Analysis.hideWhy') : t('fable5Analysis.why')}
            </button>
            {showWhy && (
              <ul className="mt-3 divide-y divide-slate-800/60 border-t border-slate-800">
                {outlook.contributions.map((c) => {
                  const strategy = analysis.strategies[c.strategy as keyof Fable5Analysis['strategies']]
                  return (
                    <li key={c.strategy} className="flex flex-wrap items-center gap-2 py-2.5">
                      <span className="w-48 text-sm font-medium">
                        {t(`fable5Analysis.strategyNames.${c.strategy}`)}
                      </span>
                      <SignalBadge signal={c.signal} />
                      {c.weight > 1 && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
                          {t('fable5Analysis.weight', { weight: c.weight })}
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
              {t('fable5Analysis.updated', { time: fmtDateTime(analysis.generated_at) })}
            </p>
          </div>

          <SupplementaryContextPanel context={analysis.context} namespace="fable5Analysis" />

          {/* Track record */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="font-semibold">{t('fable5Analysis.trackRecord.title', { market })}</h3>
            {trackRecord ? (
              <>
                <p className="mt-2 text-sm text-slate-300">
                  {t('fable5Analysis.trackRecord.summary', {
                    hitRate: trackRecord.hit_rate_pct,
                    days: trackRecord.forward_days,
                  })}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t('fable5Analysis.trackRecord.samples', {
                    samples: trackRecord.samples,
                    from: trackRecord.from,
                    to: trackRecord.to,
                  })}
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {trackRecord.avg_bullish_return_pct !== null && (
                    <span className="rounded bg-emerald-500/10 px-2 py-1 font-mono text-emerald-300">
                      {t('fable5Analysis.trackRecord.avgBullish', { value: trackRecord.avg_bullish_return_pct })}
                    </span>
                  )}
                  {trackRecord.avg_bearish_return_pct !== null && (
                    <span className="rounded bg-red-500/10 px-2 py-1 font-mono text-red-300">
                      {t('fable5Analysis.trackRecord.avgBearish', { value: trackRecord.avg_bearish_return_pct })}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="mt-2 text-sm text-slate-500">{t('fable5Analysis.trackRecord.noHistory')}</p>
            )}
          </div>
        </>
      )}

      <p className="text-xs text-slate-500">{t('fable5Analysis.disclaimer')}</p>
    </div>
  )
}
