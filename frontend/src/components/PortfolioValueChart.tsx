import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AreaSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineType,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api } from '../lib/api'
import type { PortfolioSnapshot, Trade } from '../lib/types'

const CHART_HEIGHT = 200
const REFRESH_MS = 5 * 60 * 1000 // snapshots are hourly; no need to poll often

const VALUE_COLOR = '#fbbf24' // amber, matches the total value stat card
const COUNT_COLOR = '#60a5fa'
const UP = '#34d399'
const DOWN = '#f87171'

/** Snap a trade to the latest snapshot at or before it (null = before the chart starts). */
function snapToSnapshot(tradeSec: number, snapshotTimesSec: number[]): number | null {
  if (snapshotTimesSec.length === 0 || tradeSec < snapshotTimesSec[0]) return null
  let snapped = snapshotTimesSec[0]
  for (const t of snapshotTimesSec) {
    if (t <= tradeSec) snapped = t
    else break
  }
  return snapped
}

function tradesToMarkers(
  trades: Trade[],
  snapshotTimesSec: number[],
  labels: { buy: string; sell: string },
): SeriesMarker<UTCTimestamp>[] {
  // Group same-side trades that snap to the same hourly point into one marker.
  const groups = new Map<string, { time: number; side: 'buy' | 'sell'; assets: Set<string>; count: number }>()
  for (const trade of trades) {
    const tradeSec = Math.floor(new Date(trade.created_at).getTime() / 1000)
    const time = snapToSnapshot(tradeSec, snapshotTimesSec)
    if (time === null) continue
    const key = `${time}:${trade.side}`
    const group = groups.get(key) ?? { time, side: trade.side, assets: new Set<string>(), count: 0 }
    group.assets.add(trade.market.split('-')[0])
    group.count += 1
    groups.set(key, group)
  }

  return [...groups.values()]
    .sort((a, b) => a.time - b.time)
    .map((g) => {
      const label = g.side === 'buy' ? labels.buy : labels.sell
      const text =
        g.count === 1 ? `${label} ${[...g.assets][0]}` : `${g.count}× ${label}`
      return {
        time: g.time as UTCTimestamp,
        position: g.side === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
        color: g.side === 'buy' ? UP : DOWN,
        shape: g.side === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
        text,
      }
    })
}

export default function PortfolioValueChart() {
  const { t } = useTranslation()
  const [snapshots, setSnapshots] = useState<PortfolioSnapshot[] | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [error, setError] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const valueSeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const countSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)

  useEffect(() => {
    let cancelled = false

    function load() {
      api<PortfolioSnapshot[]>('/portfolio/history')
        .then((data) => {
          if (!cancelled) {
            setSnapshots(data)
            setError(false)
          }
        })
        .catch(() => {
          if (!cancelled) setError(true)
        })
      api<Trade[]>('/trades')
        .then((data) => {
          if (!cancelled) setTrades(data)
        })
        .catch(() => {})
    }

    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  // Chart data with strictly ascending, deduplicated timestamps.
  const points = useMemo(() => {
    if (!snapshots) return null
    const seen = new Set<number>()
    const out: { time: UTCTimestamp; value: number; count: number }[] = []
    for (const s of snapshots) {
      const time = Math.floor(new Date(s.created_at).getTime() / 1000)
      if (seen.has(time)) continue
      seen.add(time)
      out.push({ time: time as UTCTimestamp, value: parseFloat(s.total_value_eur), count: s.asset_count })
    }
    return out
  }, [snapshots])

  const tradeMarkers = useMemo(() => {
    if (!points || points.length < 2) return []
    return tradesToMarkers(
      trades,
      points.map((p) => p.time as number),
      { buy: t('chart.tradeBuy'), sell: t('chart.tradeSell') },
    )
  }, [points, trades, t])

  const hasData = points !== null && points.length >= 2

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
      rightPriceScale: { borderVisible: false },
      leftPriceScale: { visible: true, borderVisible: false },
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
      valueSeriesRef.current = null
      countSeriesRef.current = null
      markersRef.current = null
    }
  }, [])

  // Replace series whenever the data changes.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    if (markersRef.current) {
      markersRef.current.setMarkers([])
      markersRef.current = null
    }
    if (valueSeriesRef.current) {
      chart.removeSeries(valueSeriesRef.current)
      valueSeriesRef.current = null
    }
    if (countSeriesRef.current) {
      chart.removeSeries(countSeriesRef.current)
      countSeriesRef.current = null
    }

    if (!points || points.length < 2) return

    const valueSeries = chart.addSeries(AreaSeries, {
      lineColor: VALUE_COLOR,
      topColor: `${VALUE_COLOR}40`,
      bottomColor: `${VALUE_COLOR}00`,
      lineWidth: 2,
      priceScaleId: 'right',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    valueSeries.setData(points.map((p) => ({ time: p.time, value: p.value })))
    valueSeriesRef.current = valueSeries

    const countSeries = chart.addSeries(LineSeries, {
      color: COUNT_COLOR,
      lineWidth: 1,
      lineType: LineType.WithSteps,
      priceScaleId: 'left',
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })
    countSeries.setData(points.map((p) => ({ time: p.time, value: p.count })))
    countSeriesRef.current = countSeries

    if (tradeMarkers.length > 0) {
      markersRef.current = createSeriesMarkers(valueSeries, tradeMarkers)
    }

    chart.timeScale().fitContent()
  }, [points, tradeMarkers])

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{t('portfolio.valueChartTitle')}</h2>
        {hasData && (
          <span className="flex items-center gap-3 text-xs text-slate-400">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: VALUE_COLOR }} />
              {t('portfolio.totalValue')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: COUNT_COLOR }} />
              {t('portfolio.assetsHeldLegend')}
            </span>
            {tradeMarkers.length > 0 && (
              <span className="flex items-center gap-2">
                <span className="text-emerald-400">▲ {t('chart.tradeBuy')}</span>
                <span className="text-red-400">▼ {t('chart.tradeSell')}</span>
              </span>
            )}
          </span>
        )}
      </div>

      <div className="relative w-full">
        {error && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-slate-900/80">
            <p className="text-sm text-slate-500">{t('chart.loadError')}</p>
          </div>
        )}
        {!error && snapshots === null && (
          <div className="absolute inset-0 z-10 animate-pulse rounded-md bg-slate-800/40" />
        )}
        {!error && snapshots !== null && !hasData && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-slate-900/80 px-4">
            <p className="text-center text-sm text-slate-500">{t('portfolio.valueChartEmpty')}</p>
          </div>
        )}
        <div ref={containerRef} className="w-full" role="img" aria-label={t('portfolio.valueChartTitle')} />
      </div>
    </div>
  )
}
