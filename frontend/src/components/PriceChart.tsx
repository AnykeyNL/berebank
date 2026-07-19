import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type AutoscaleInfo,
} from 'lightweight-charts'
import { api } from '../lib/api'
import { fmtPct, fmtPrice } from '../lib/format'

// Bitvavo candle: [timestamp_ms, open, high, low, close, volume]
type Candle = [number, string, string, string, string, string]

type ChartRange = '1h' | '1d' | '1w' | '30d' | '365d'
type ChartType = 'line' | 'candle'

const RANGES: ChartRange[] = ['1h', '1d', '1w', '30d', '365d']
const CHART_HEIGHT = 180

const UP = '#34d399'
const DOWN = '#f87171'

export type LimitOrderMarker = {
  id: number
  side: 'buy' | 'sell'
  price: number
}

export default function PriceChart({
  market,
  limitOrders = [],
  lastPrice = null,
}: {
  market: string
  limitOrders?: LimitOrderMarker[]
  lastPrice?: number | null
}) {
  const { t } = useTranslation()
  const [candles, setCandles] = useState<Candle[] | null>(null)
  const [error, setError] = useState(false)
  const [range, setRange] = useState<ChartRange>('1d')
  const [chartType, setChartType] = useState<ChartType>('line')

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | ISeriesApi<'Candlestick'> | null>(null)
  const limitOrdersRef = useRef(limitOrders)
  const lastPriceRef = useRef(lastPrice)
  limitOrdersRef.current = limitOrders
  lastPriceRef.current = lastPrice

  useEffect(() => {
    let cancelled = false
    setCandles(null)
    setError(false)

    function load() {
      api<Candle[]>(`/markets/${encodeURIComponent(market)}/candles?range=${range}`)
        .then((data) => {
          if (!cancelled) {
            setCandles(data)
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

  const stats = useMemo(() => {
    if (!candles || candles.length < 2) return null
    const closes = candles.map((c) => parseFloat(c[4]))
    const lows = candles.map((c) => parseFloat(c[3]))
    const highs = candles.map((c) => parseFloat(c[2]))
    const min = Math.min(...lows)
    const max = Math.max(...highs)
    const first = closes[0]
    const lastClose = closes[closes.length - 1]
    const changePct = first !== 0 ? ((lastClose - first) / first) * 100 : null
    const up = lastClose >= first
    return { min, max, changePct, up, lastClose }
  }, [candles])

  const chartExtent = useMemo(() => {
    if (!stats) return null
    let min = stats.min
    let max = stats.max
    if (lastPrice !== null && Number.isFinite(lastPrice)) {
      min = Math.min(min, lastPrice)
      max = Math.max(max, lastPrice)
    }
    for (const order of limitOrders) {
      min = Math.min(min, order.price)
      max = Math.max(max, order.price)
    }
    return { ...stats, min, max }
  }, [stats, limitOrders, lastPrice])

  const autoscaleInfoProvider = useMemo(() => {
    return (original: () => AutoscaleInfo | null) => {
      const extraPrices = [
        ...limitOrdersRef.current.map((o) => o.price),
        ...(lastPriceRef.current !== null && Number.isFinite(lastPriceRef.current)
          ? [lastPriceRef.current]
          : []),
      ]
      if (extraPrices.length === 0) return original()
      const res = original()
      if (res === null || res.priceRange === null) {
        const min = Math.min(...extraPrices)
        const max = Math.max(...extraPrices)
        return { priceRange: { minValue: min, maxValue: max } }
      }
      let { minValue, maxValue } = res.priceRange
      for (const price of extraPrices) {
        minValue = Math.min(minValue, price)
        maxValue = Math.max(maxValue, price)
      }
      return { ...res, priceRange: { minValue, maxValue } }
    }
  }, [])

  // Create chart once; resize with the container.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const chart = createChart(el, {
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(51, 65, 85, 0.4)' },
        horzLines: { color: 'rgba(51, 65, 85, 0.4)' },
      },
      rightPriceScale: {
        borderVisible: false,
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
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
      seriesRef.current = null
    }
  }, [])

  // Keep time-axis options in sync with the selected range.
  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: {
        timeVisible: true,
        secondsVisible: range === '1h',
      },
    })
  }, [range])

  // Replace series whenever candles or chart type change.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (seriesRef.current) {
      chart.removeSeries(seriesRef.current)
      seriesRef.current = null
    }

    if (!candles || candles.length < 2 || !stats) return

    const color = stats.up ? UP : DOWN

    if (chartType === 'candle') {
      const series = chart.addSeries(CandlestickSeries, {
        upColor: UP,
        downColor: DOWN,
        borderUpColor: UP,
        borderDownColor: DOWN,
        wickUpColor: UP,
        wickDownColor: DOWN,
        autoscaleInfoProvider,
      })
      series.setData(
        candles.map((c) => ({
          time: Math.floor(c[0] / 1000) as UTCTimestamp,
          open: parseFloat(c[1]),
          high: parseFloat(c[2]),
          low: parseFloat(c[3]),
          close: parseFloat(c[4]),
        })),
      )
      seriesRef.current = series
    } else {
      const series = chart.addSeries(AreaSeries, {
        lineColor: color,
        topColor: `${color}40`,
        bottomColor: `${color}00`,
        lineWidth: 2,
        autoscaleInfoProvider,
      })
      series.setData(
        candles.map((c) => ({
          time: Math.floor(c[0] / 1000) as UTCTimestamp,
          value: parseFloat(c[4]),
        })),
      )
      seriesRef.current = series
    }

    for (const order of limitOrders) {
      seriesRef.current.createPriceLine({
        price: order.price,
        color: order.side === 'buy' ? UP : DOWN,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: order.side === 'buy' ? t('chart.limitBuy') : t('chart.limitSell'),
      })
    }

    chart.timeScale().fitContent()
  }, [candles, chartType, stats, limitOrders, autoscaleInfoProvider, t])

  // Re-run autoscale when limit orders or live price move outside the candle range.
  useEffect(() => {
    chartRef.current?.priceScale('right').setAutoScale(true)
  }, [limitOrders, lastPrice, candles, chartType])

  const btnBase =
    'rounded px-2 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500'
  const btnActive = 'bg-slate-700 text-slate-100'
  const btnIdle = 'text-slate-400 hover:text-slate-200'

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5" role="group" aria-label={t('chart.range')}>
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
          <div className="flex gap-0.5 rounded-md bg-slate-800/60 p-0.5" role="group" aria-label={t('chart.type')}>
            {(['line', 'candle'] as const).map((type) => (
              <button
                key={type}
                type="button"
                className={`${btnBase} ${chartType === type ? btnActive : btnIdle}`}
                onClick={() => setChartType(type)}
                aria-pressed={chartType === type}
              >
                {t(`chart.${type}`)}
              </button>
            ))}
          </div>
        </div>
        {stats && (
          <span
            className={`text-sm font-medium ${
              stats.changePct === null ? 'text-slate-500' : stats.up ? 'text-emerald-400' : 'text-red-400'
            }`}
          >
            {fmtPct(stats.changePct)}
          </span>
        )}
      </div>

      <div className="relative w-full">
        {error && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-slate-900/80">
            <p className="text-sm text-slate-500">{t('chart.loadError')}</p>
          </div>
        )}
        {!error && (!candles || candles.length < 2) && (
          <div className="absolute inset-0 z-10 animate-pulse rounded-md bg-slate-800/40" />
        )}
        <div
          ref={containerRef}
          className="w-full"
          role="img"
          aria-label={`${market} ${t(`chart.ranges.${range}`)} ${t(`chart.${chartType}`)}`}
        />
      </div>
      {chartExtent && !error && (
        <div className="mt-2 flex justify-between text-xs text-slate-500">
          <span>
            {t('chart.low')} {fmtPrice(chartExtent.min)}
          </span>
          <span>
            {t('chart.high')} {fmtPrice(chartExtent.max)}
          </span>
        </div>
      )}
    </div>
  )
}
