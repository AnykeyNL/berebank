import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { AnalysisSignal, IndicatorPoint } from '../lib/types'

const SIGNAL_STYLES: Record<AnalysisSignal, string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400',
  bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-300',
  none: 'bg-slate-800 text-slate-500',
}

export function SignalBadge({ signal }: { signal: AnalysisSignal }) {
  const { t } = useTranslation()
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${SIGNAL_STYLES[signal]}`}>
      {t(`analyze.signals.${signal}`)}
    </span>
  )
}

export default function AnalysisCard({
  title,
  signal,
  reason,
  explanation,
  stats,
  children,
}: {
  title: string
  signal: AnalysisSignal
  reason: string
  explanation: string
  stats: { label: string; value: string }[]
  children?: React.ReactNode
}) {
  const { t } = useTranslation()
  const [showExplanation, setShowExplanation] = useState(false)
  const muted = signal === 'none'

  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 p-4 ${muted ? 'opacity-70' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        <SignalBadge signal={signal} />
      </div>
      <p className="mt-2 text-sm text-slate-300">{reason}</p>

      {stats.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
          {stats.map((s) => (
            <div key={s.label}>
              <dt className="text-xs uppercase tracking-wide text-slate-500">{s.label}</dt>
              <dd className="mt-0.5 font-mono text-sm">{s.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {children}

      <button
        type="button"
        onClick={() => setShowExplanation((v) => !v)}
        className="mt-3 text-xs font-medium text-amber-400 hover:text-amber-300"
        aria-expanded={showExplanation}
      >
        {showExplanation ? t('analyze.hideExplanation') : t('analyze.howItWorks')}
      </button>
      {showExplanation && <p className="mt-2 text-xs leading-relaxed text-slate-400">{explanation}</p>}
    </div>
  )
}

function toLine(points: IndicatorPoint[]) {
  return points
    .filter((p): p is [number, string] => p[1] !== null)
    .map((p) => ({ time: Math.floor(p[0] / 1000) as UTCTimestamp, value: parseFloat(p[1]) }))
}

const CHART_OPTIONS = {
  height: 110,
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#64748b',
    attributionLogo: false,
  },
  grid: {
    vertLines: { visible: false },
    horzLines: { color: 'rgba(51, 65, 85, 0.3)' },
  },
  rightPriceScale: { borderVisible: false },
  timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  crosshair: {
    vertLine: { color: 'rgba(148, 163, 184, 0.4)' },
    horzLine: { color: 'rgba(148, 163, 184, 0.4)' },
  },
} as const

/** Small sub-chart for oscillator indicators (RSI, MACD) inside a card. */
export function IndicatorChart({
  kind,
  series,
}: {
  kind: 'rsi' | 'macd'
  series: Record<string, IndicatorPoint[]>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, CHART_OPTIONS)
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
    // Rebuild all series when the data changes (cheap at this size).
    for (const s of seriesRef.current) chart.removeSeries(s)
    seriesRef.current = []

    if (kind === 'rsi') {
      const rsi = chart.addSeries(LineSeries, {
        color: '#fbbf24',
        lineWidth: 2,
        priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
      })
      rsi.setData(toLine(series.rsi ?? []))
      for (const level of [30, 70]) {
        rsi.createPriceLine({
          price: level,
          color: 'rgba(148, 163, 184, 0.5)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: '',
        })
      }
      seriesRef.current = [rsi]
    } else {
      const hist = chart.addSeries(HistogramSeries, { priceFormat: { type: 'price', precision: 2, minMove: 0.01 } })
      hist.setData(
        toLine(series.histogram ?? []).map((p) => ({
          ...p,
          color: p.value >= 0 ? 'rgba(52, 211, 153, 0.6)' : 'rgba(248, 113, 113, 0.6)',
        })),
      )
      const macdLine = chart.addSeries(LineSeries, { color: '#fbbf24', lineWidth: 1 })
      macdLine.setData(toLine(series.macd ?? []))
      const signalLine = chart.addSeries(LineSeries, { color: '#60a5fa', lineWidth: 1 })
      signalLine.setData(toLine(series.signal ?? []))
      seriesRef.current = [hist, macdLine, signalLine]
    }
    chart.timeScale().fitContent()
  }, [kind, series])

  return <div ref={containerRef} className="mt-3 w-full" />
}
