import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
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
import { usePrices } from '../lib/usePrices'
import { chartPriceFormat, fmtDateTime, fmtPct, fmtPrice } from '../lib/format'
import type { Analysis, AnalysisRange, AnalysisStrategy, Market } from '../lib/types'
import AnalysisCard, { IndicatorChart } from '../components/AnalysisCard'
import AssetClassIcon from '../components/AssetClassIcon'

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

function AnalysisChart({ analysis, overlays }: { analysis: Analysis; overlays: Set<Overlay> }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([])

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

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    for (const s of seriesRef.current) chart.removeSeries(s)
    seriesRef.current = []

    const candles = analysis.candles
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

    chart.timeScale().fitContent()
  }, [analysis, overlays])

  return <div ref={containerRef} className="w-full" />
}

/** Format backend reason params into human-readable values for i18n interpolation. */
function formatReasonParams(
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
  const market = (marketParam ?? 'BTC-EUR').toUpperCase()
  const { prices } = usePrices()

  const [range, setRange] = useState<AnalysisRange>('30d')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [error, setError] = useState(false)
  const [markets, setMarkets] = useState<Market[]>([])
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(['sma', 'levels']))

  useEffect(() => {
    api<Market[]>('/markets').then(setMarkets).catch(() => {})
  }, [])

  useEffect(() => {
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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <Link to={`/trade/${market}`} className="text-xs text-amber-400 hover:text-amber-300">
              ← {t('analyze.backToTrade')}
            </Link>
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
          {!error && analysis && <AnalysisChart analysis={analysis} overlays={overlays} />}
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
