import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api } from '../lib/api'
import { attachHistoryTrigger, shouldFitContent, useOlderHistory } from '../lib/chartHistory'
import { usePrices } from '../lib/usePrices'
import { chartPriceFormat, fmtDateTime, fmtPct, fmtPrice } from '../lib/format'
import type {
  Analysis,
  AnalysisRange,
  AnalysisStrategy,
  AssetClass,
  Market,
  OutlookConfidence,
  TechnicalOutlooks,
} from '../lib/types'
import AnalysisCard, { IndicatorChart, SignalBadge } from '../components/AnalysisCard'
import AnalysisCrossLinks from '../components/AnalysisCrossLinks'
import AssetClassIcon from '../components/AssetClassIcon'

const CONFIDENCE_ORDER: OutlookConfidence[] = ['low', 'medium', 'high']
const DIRECTION_RANK: Record<string, number> = { bullish: 3, neutral: 2, bearish: 1, none: 0 }
const CONFIDENCE_RANK: Record<OutlookConfidence, number> = { high: 3, medium: 2, low: 1 }

type SortKey = 'asset' | 'confidence' | 'score' | 'direction' | 'last'
type SortDir = 'asc' | 'desc'

function ConfidenceDots({ confidence }: { confidence: OutlookConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <span
      className="hidden items-center gap-0.5 sm:flex"
      title={`${t('analyze.outlook.confidenceLabel')}: ${t(`analyze.outlook.confidenceLevels.${confidence}`)}`}
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
  const [outlooks, setOutlooks] = useState<TechnicalOutlooks['outlooks']>({})
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<'all' | AssetClass>('all')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'score', dir: 'desc' })

  useEffect(() => {
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
    api<TechnicalOutlooks>('/markets/technical-outlooks')
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
        <h2 className="px-1 pb-2 text-lg font-bold">{t('analyze.listTitle')}</h2>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('trade.searchPlaceholder')}
          aria-label={t('analyze.pickAsset')}
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
          {sortHeader('asset', t('analyze.table.asset'), 'flex-1')}
          {sortHeader(
            'confidence',
            <span className="flex items-center gap-0.5" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 rounded-full bg-current" />
              ))}
            </span>,
            'hidden w-12 justify-center sm:flex',
            t('analyze.outlook.confidenceLabel'),
          )}
          {sortHeader('score', t('analyze.outlook.scoreLabel'), 'w-12 justify-end sm:w-14')}
          {sortHeader('direction', t('analyze.table.outlook'), 'w-20 justify-center sm:w-24')}
          {sortHeader('last', t('trade.last'), 'w-16 justify-end sm:w-20')}
        </div>
        {sortedRows.map((m) => {
          const last = prices[m.market]?.last ?? m.last
          const outlook = outlooks[m.market]
          return (
            <button
              key={m.market}
              type="button"
              onClick={() => navigate(`/technical-analysis/${m.market}`)}
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
                    {t('analyze.table.collecting')}
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

const RANGES: AnalysisRange[] = ['1d', '1w', '30d', '90d', '180d', '365d']

const UP = '#34d399'
const DOWN = '#f87171'

type Overlay = 'sma' | 'ema' | 'bollinger' | 'levels'
const OVERLAYS: Overlay[] = ['sma', 'ema', 'bollinger', 'levels']

const OVERLAY_LINES: Record<'sma' | 'ema' | 'bollinger', { key: string; strategy: 'trend' | 'volatility'; color: string; style: LineStyle }[]> = {
  sma: [
    { key: 'sma20', strategy: 'trend', color: '#fbbf24', style: LineStyle.Solid },
    { key: 'sma50', strategy: 'trend', color: '#c084fc', style: LineStyle.Solid },
  ],
  ema: [
    { key: 'ema12', strategy: 'trend', color: '#22d3ee', style: LineStyle.Dashed },
    { key: 'ema26', strategy: 'trend', color: '#60a5fa', style: LineStyle.Dashed },
  ],
  bollinger: [
    { key: 'bb_upper', strategy: 'volatility', color: 'rgba(148, 163, 184, 0.7)', style: LineStyle.Dotted },
    { key: 'bb_middle', strategy: 'volatility', color: 'rgba(148, 163, 184, 0.4)', style: LineStyle.Dotted },
    { key: 'bb_lower', strategy: 'volatility', color: 'rgba(148, 163, 184, 0.7)', style: LineStyle.Dotted },
  ],
}

function AnalysisChart({
  analysis,
  overlays,
  market,
  range,
}: {
  analysis: Analysis
  overlays: Set<Overlay>
  market: string
  range: AnalysisRange
}) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([])
  const renderedCountRef = useRef(0)
  const renderedOlderRef = useRef(0)

  // Zooming out pages in older bars ahead of the analysis window.
  const { bars, olderCount, loadOlder, loading: loadingHistory, canLoadMore } = useOlderHistory({
    market,
    range,
    baseBars: analysis.candles,
  })
  const loadOlderRef = useRef(loadOlder)
  const canLoadRef = useRef(canLoadMore)
  loadOlderRef.current = loadOlder
  canLoadRef.current = canLoadMore && !loadingHistory

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(51, 65, 85, 0.4)' },
        horzLines: { color: 'rgba(51, 65, 85, 0.4)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: 'rgba(148, 163, 184, 0.4)' },
        horzLine: { color: 'rgba(148, 163, 184, 0.4)' },
      },
    })
    chartRef.current = chart
    const ro = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width) chart.applyOptions({ width })
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = []
    }
  }, [])

  // Page in older bars when the viewport reaches the left edge.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    return attachHistoryTrigger(
      chart,
      () => canLoadRef.current,
      () => loadOlderRef.current(),
    )
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    // Captured before the series is replaced: prepending bars shifts logical
    // indices, so the viewport is remembered as timestamps instead.
    const timeScale = chart.timeScale()
    const fit = shouldFitContent(
      renderedOlderRef.current,
      timeScale.getVisibleLogicalRange(),
      renderedCountRef.current,
    )
    const keptRange = fit ? null : timeScale.getVisibleRange()

    for (const s of seriesRef.current) chart.removeSeries(s)
    seriesRef.current = []

    const candles = bars
    if (candles.length < 2) return
    const lastClose = parseFloat(candles[candles.length - 1][4])

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceFormat: chartPriceFormat(lastClose),
    })
    candleSeries.setData(
      candles.map((c) => ({
        time: Math.floor(c[0] / 1000) as UTCTimestamp,
        open: parseFloat(c[1]),
        high: parseFloat(c[2]),
        low: parseFloat(c[3]),
        close: parseFloat(c[4]),
      })),
    )
    seriesRef.current.push(candleSeries)

    for (const overlay of ['sma', 'ema', 'bollinger'] as const) {
      if (!overlays.has(overlay)) continue
      for (const line of OVERLAY_LINES[overlay]) {
        const points = analysis.strategies[line.strategy].series[line.key] ?? []
        const data = points
          .filter((p): p is [number, string] => p[1] !== null)
          .map((p) => ({ time: Math.floor(p[0] / 1000) as UTCTimestamp, value: parseFloat(p[1]) }))
        if (data.length === 0) continue
        const s = chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: 1,
          lineStyle: line.style,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        s.setData(data)
        seriesRef.current.push(s)
      }
    }

    if (overlays.has('levels')) {
      for (const level of analysis.strategies.levels_volume.levels ?? []) {
        if (level.price === null) continue
        const price = parseFloat(level.price)
        candleSeries.createPriceLine({
          price,
          color: price <= lastClose ? 'rgba(52, 211, 153, 0.6)' : 'rgba(248, 113, 113, 0.6)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: '',
        })
      }
    }

    renderedCountRef.current = candles.length
    renderedOlderRef.current = olderCount
    if (keptRange === null) timeScale.fitContent()
    else timeScale.setVisibleRange(keptRange)
  }, [analysis, overlays, bars, olderCount])

  return (
    <div className="relative w-full">
      {loadingHistory && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-slate-900/80 px-2 py-1 text-[10px] text-slate-400">
          {t('chart.loadingHistory')}
        </div>
      )}
      <div ref={containerRef} className="w-full" />
    </div>
  )
}

/** Format backend reason params into human-readable values for i18n interpolation. */
export function formatReasonParams(
  params: Record<string, string | number | null>,
  t: (key: string) => string,
): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === null) {
      out[key] = '—'
    } else if (key === 'direction') {
      out[key] = t(`analyze.direction.${value}`)
    } else if (key === 'volume_state') {
      out[key] = t(`analyze.volumeState.${value}`)
    } else if (key === 'bars_ago') {
      out[key] = value
    } else if (key === 'rsi' || key.endsWith('_pct')) {
      out[key] = parseFloat(String(value)).toFixed(1)
    } else {
      // Remaining params are EUR prices
      out[key] = fmtPrice(String(value))
    }
  }
  return out
}

export default function AnalyzePage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const market = (marketParam ?? '').toUpperCase()
  const { prices } = usePrices()

  const [range, setRange] = useState<AnalysisRange>('30d')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [error, setError] = useState(false)
  const [markets, setMarkets] = useState<Market[]>([])
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(['sma', 'levels']))

  useEffect(() => {
    if (!market) return
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
  }, [market])

  useEffect(() => {
    if (!market) return
    let cancelled = false
    setAnalysis(null)
    setError(false)

    function load() {
      api<Analysis>(`/markets/${encodeURIComponent(market)}/analysis?range=${range}`)
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

  function reasonText(strategy: AnalysisStrategy): string {
    return t(`analyze.reasons.${strategy.reason.code}`, formatReasonParams(strategy.reason.params, t))
  }

  function stats(key: keyof Analysis['strategies']): { label: string; value: string }[] {
    if (!analysis) return []
    const v = analysis.strategies[key].values
    switch (key) {
      case 'trend':
        return [
          { label: 'SMA 20', value: fmtPrice(v.sma20) },
          { label: 'SMA 50', value: fmtPrice(v.sma50) },
          { label: 'EMA 12', value: fmtPrice(v.ema12) },
          { label: 'EMA 26', value: fmtPrice(v.ema26) },
        ]
      case 'rsi':
        return v.rsi
          ? [
              { label: 'RSI 14', value: parseFloat(v.rsi).toFixed(1) },
              { label: t('analyze.stats.direction'), value: t(`analyze.direction.${v.direction}`) },
            ]
          : []
      case 'macd':
        return [
          { label: 'MACD', value: fmtPrice(v.macd) },
          { label: t('analyze.stats.signalLine'), value: fmtPrice(v.signal) },
          { label: t('analyze.stats.histogram'), value: fmtPrice(v.histogram) },
        ]
      case 'volatility':
        return [
          { label: t('analyze.stats.bbUpper'), value: fmtPrice(v.bb_upper) },
          { label: t('analyze.stats.bbLower'), value: fmtPrice(v.bb_lower) },
          {
            label: 'ATR 14',
            value: v.atr_pct ? `${fmtPrice(v.atr)} (${parseFloat(v.atr_pct).toFixed(2)}%)` : fmtPrice(v.atr),
          },
          { label: t('analyze.stats.suggestedStop'), value: fmtPrice(v.suggested_stop) },
        ]
      case 'levels_volume':
        return [
          { label: t('analyze.stats.support'), value: fmtPrice(v.support) },
          { label: t('analyze.stats.resistance'), value: fmtPrice(v.resistance) },
          {
            label: t('analyze.stats.volume'),
            value: v.volume_state ? t(`analyze.volumeState.${v.volume_state}`) : '—',
          },
        ]
    }
  }

  const btnBase =
    'rounded px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500 md:px-2 md:py-1'
  const btnActive = 'bg-slate-700 text-slate-100'
  const btnIdle = 'text-slate-400 hover:text-slate-200'

  if (!market) return <AssetPicker />

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3 text-xs">
              <Link to={`/trade/${market}`} className="text-amber-400 hover:text-amber-300">
                ← {t('analyze.backToTrade')}
              </Link>
              <Link to="/technical-analysis" className="text-slate-400 hover:text-slate-200">
                {t('analyze.changeAsset')}
              </Link>
              <AnalysisCrossLinks market={market} current="technical" />
            </div>
            <h2 className="mt-1 flex items-center gap-2 text-xl font-bold">
              {marketInfo && <AssetClassIcon assetClass={marketInfo.asset_class} className="h-5 w-5" />}
              {t('analyze.pageTitle', { market })}
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
      </div>

      {/* Chart with overlays */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div
            className="flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5"
            role="group"
            aria-label={t('analyze.range')}
          >
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                className={`${btnBase} ${range === r ? btnActive : btnIdle}`}
                onClick={() => setRange(r)}
                aria-pressed={range === r}
              >
                {t(`chart.ranges.${r}`)}
              </button>
            ))}
          </div>
          <div
            className="flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5"
            role="group"
            aria-label={t('analyze.overlaysLabel')}
          >
            {OVERLAYS.map((o) => (
              <button
                key={o}
                type="button"
                className={`${btnBase} ${overlays.has(o) ? btnActive : btnIdle}`}
                onClick={() =>
                  setOverlays((prev) => {
                    const next = new Set(prev)
                    if (next.has(o)) next.delete(o)
                    else next.add(o)
                    return next
                  })
                }
                aria-pressed={overlays.has(o)}
              >
                {t(`analyze.overlays.${o}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="relative w-full">
          {error && (
            <div className="flex h-80 items-center justify-center">
              <p className="text-sm text-slate-500">{t('analyze.loadError')}</p>
            </div>
          )}
          {!error && !analysis && <div className="h-80 animate-pulse rounded-md bg-slate-800/40" />}
          {!error && analysis && (
            <AnalysisChart analysis={analysis} overlays={overlays} market={market} range={range} />
          )}
        </div>
        {analysis && (
          <p className="mt-2 text-xs text-slate-500">
            {t('analyze.updated', { time: fmtDateTime(analysis.generated_at) })}
          </p>
        )}
      </div>

      {/* Strategy cards */}
      {analysis && (
        <div className="grid gap-4 md:grid-cols-2">
          <AnalysisCard
            title={t('analyze.strategies.trend.title')}
            signal={analysis.strategies.trend.signal}
            reason={reasonText(analysis.strategies.trend)}
            explanation={t('analyze.strategies.trend.explanation')}
            stats={stats('trend')}
          />
          <AnalysisCard
            title={t('analyze.strategies.rsi.title')}
            signal={analysis.strategies.rsi.signal}
            reason={reasonText(analysis.strategies.rsi)}
            explanation={t('analyze.strategies.rsi.explanation')}
            stats={stats('rsi')}
          >
            {analysis.strategies.rsi.signal !== 'none' && (
              <IndicatorChart kind="rsi" series={analysis.strategies.rsi.series} />
            )}
          </AnalysisCard>
          <AnalysisCard
            title={t('analyze.strategies.macd.title')}
            signal={analysis.strategies.macd.signal}
            reason={reasonText(analysis.strategies.macd)}
            explanation={t('analyze.strategies.macd.explanation')}
            stats={stats('macd')}
          >
            {analysis.strategies.macd.signal !== 'none' && (
              <IndicatorChart kind="macd" series={analysis.strategies.macd.series} />
            )}
          </AnalysisCard>
          <AnalysisCard
            title={t('analyze.strategies.volatility.title')}
            signal={analysis.strategies.volatility.signal}
            reason={reasonText(analysis.strategies.volatility)}
            explanation={t('analyze.strategies.volatility.explanation')}
            stats={stats('volatility')}
          />
          <AnalysisCard
            title={t('analyze.strategies.levels_volume.title')}
            signal={analysis.strategies.levels_volume.signal}
            reason={reasonText(analysis.strategies.levels_volume)}
            explanation={t('analyze.strategies.levels_volume.explanation')}
            stats={stats('levels_volume')}
          />
        </div>
      )}

      <p className="text-xs text-slate-500">{t('analyze.disclaimer')}</p>
    </div>
  )
}
