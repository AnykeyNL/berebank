import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineStyle,
  type AutoscaleInfo,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api } from '../lib/api'
import { chartPriceFormat, fmtPct, fmtPrice } from '../lib/format'

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
  kind?: 'limit' | 'stop_loss'
}

export type TradeMarker = {
  id: number
  side: 'buy' | 'sell'
  price: number
  created_at: string
}

function snapTradeTime(tradeSec: number, candleTimesSec: number[]): UTCTimestamp | null {
  if (candleTimesSec.length === 0) return null

  const first = candleTimesSec[0]
  const last = candleTimesSec[candleTimesSec.length - 1]
  const interval =
    candleTimesSec.length >= 2 ? candleTimesSec[candleTimesSec.length - 1] - candleTimesSec[candleTimesSec.length - 2] : 60

  if (tradeSec < first || tradeSec > last + interval) return null

  let snapped = first
  for (const t of candleTimesSec) {
    if (t <= tradeSec) snapped = t
    else break
  }
  return snapped as UTCTimestamp
}

function tradesToMarkers(
  trades: TradeMarker[],
  candles: Candle[],
  labels: { buy: string; sell: string },
): SeriesMarker<UTCTimestamp>[] {
  const candleTimesSec = candles.map((c) => Math.floor(c[0] / 1000))
  const markers: SeriesMarker<UTCTimestamp>[] = []

  for (const trade of trades) {
    const tradeSec = Math.floor(new Date(trade.created_at).getTime() / 1000)
    const time = snapTradeTime(tradeSec, candleTimesSec)
    if (time === null) continue

    const isBuy = trade.side === 'buy'
    markers.push({
      time,
      position: isBuy ? 'atPriceBottom' : 'atPriceTop',
      price: trade.price,
      color: isBuy ? UP : DOWN,
      shape: isBuy ? 'arrowUp' : 'arrowDown',
      text: isBuy ? labels.buy : labels.sell,
    })
  }

  return markers
}

export default function PriceChart({
  market,
  limitOrders = [],
  trades = [],
  lastPrice = null,
}: {
  market: string
  limitOrders?: LimitOrderMarker[]
  trades?: TradeMarker[]
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
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const limitOrdersRef = useRef(limitOrders)
  const tradesRef = useRef(trades)
  const lastPriceRef = useRef(lastPrice)
  limitOrdersRef.current = limitOrders
  tradesRef.current = trades
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

  const tradeMarkers = useMemo(() => {
    if (!candles || candles.length < 2) return []
    return tradesToMarkers(trades, candles, {
      buy: t('chart.tradeBuy'),
      sell: t('chart.tradeSell'),
    })
  }, [candles, trades, t])

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
    for (const trade of trades) {
      min = Math.min(min, trade.price)
      max = Math.max(max, trade.price)
    }
    return { ...stats, min, max }
  }, [stats, limitOrders, trades, lastPrice])

  const autoscaleInfoProvider = useMemo(() => {
    return (original: () => AutoscaleInfo | null) => {
      const extraPrices = [
        ...limitOrdersRef.current.map((o) => o.price),
        ...tradesRef.current.map((tr) => tr.price),
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
      markersRef.current = null
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

    if (markersRef.current) {
      markersRef.current.setMarkers([])
      markersRef.current = null
    }
    if (seriesRef.current) {
      chart.removeSeries(seriesRef.current)
      seriesRef.current = null
    }

    if (!candles || candles.length < 2 || !stats) return

    const color = stats.up ? UP : DOWN
    const priceFormat = chartPriceFormat(lastPrice ?? stats.lastClose)

    if (chartType === 'candle') {
      const series = chart.addSeries(CandlestickSeries, {
        upColor: UP,
        downColor: DOWN,
        borderUpColor: UP,
        borderDownColor: DOWN,
        wickUpColor: UP,
        wickDownColor: DOWN,
        autoscaleInfoProvider,
        priceFormat,
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
        priceFormat,
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
      const isStopLoss = order.kind === 'stop_loss'
      seriesRef.current.createPriceLine({
        price: order.price,
        color: isStopLoss ? '#fbbf24' : order.side === 'buy' ? UP : DOWN,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: isStopLoss
          ? t('chart.stopLoss')
          : order.side === 'buy'
            ? t('chart.limitBuy')
            : t('chart.limitSell'),
      })
    }

    if (tradeMarkers.length > 0 && seriesRef.current) {
      markersRef.current = createSeriesMarkers(seriesRef.current, tradeMarkers)
    }

    chart.timeScale().fitContent()
  }, [candles, chartType, stats, limitOrders, tradeMarkers, autoscaleInfoProvider, lastPrice, t])

  // Keep axis precision in sync when the live price crosses a formatting tier.
  useEffect(() => {
    const ref = lastPrice ?? stats?.lastClose
    if (!seriesRef.current || ref === null || ref === undefined || !Number.isFinite(ref)) return
    seriesRef.current.applyOptions({ priceFormat: chartPriceFormat(ref) })
  }, [lastPrice, stats?.lastClose])

  // Re-run autoscale when limit orders, trades, or live price move outside the candle range.
  useEffect(() => {
    chartRef.current?.priceScale('right').setAutoScale(true)
  }, [limitOrders, trades, lastPrice, candles, chartType])

  const btnBase =
    'rounded px-2 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500'
  const btnActive = 'bg-slate-700 text-slate-100'
  const btnIdle = 'text-slate-400 hover:text-slate-200'

  const hasTradeLegend = trades.length > 0 && tradeMarkers.length > 0

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
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-slate-500">
          <span>
            {t('chart.low')} {fmtPrice(chartExtent.min)}
          </span>
          {hasTradeLegend && (
            <span className="flex items-center gap-3">
              <span className="text-emerald-400">▲ {t('chart.tradeBuy')}</span>
              <span className="text-red-400">▼ {t('chart.tradeSell')}</span>
            </span>
          )}
          <span>
            {t('chart.high')} {fmtPrice(chartExtent.max)}
          </span>
        </div>
      )}
    </div>
  )
}
