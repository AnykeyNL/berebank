import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { fmtPct, fmtPrice } from '../lib/format'

// Bitvavo candle: [timestamp_ms, open, high, low, close, volume]
type Candle = [number, string, string, string, string, string]

const W = 800
const H = 160

export default function PriceChart({ market }: { market: string }) {
  const { t } = useTranslation()
  const [candles, setCandles] = useState<Candle[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setCandles(null)
    setError(false)

    function load() {
      api<Candle[]>(`/markets/${market}/candles`)
        .then((data) => {
          if (!cancelled) setCandles(data)
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
  }, [market])

  const chart = useMemo(() => {
    if (!candles || candles.length < 2) return null
    const closes = candles.map((c) => parseFloat(c[4]))
    const lows = candles.map((c) => parseFloat(c[3]))
    const highs = candles.map((c) => parseFloat(c[2]))
    const min = Math.min(...lows)
    const max = Math.max(...highs)
    const span = max - min || 1

    const x = (i: number) => (i / (closes.length - 1)) * W
    const y = (v: number) => H - ((v - min) / span) * (H - 8) - 4

    const line = closes.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
    const area = `0,${H} ${line} ${W},${H}`

    const first = closes[0]
    const lastClose = closes[closes.length - 1]
    const changePct = first !== 0 ? ((lastClose - first) / first) * 100 : null
    const up = lastClose >= first

    return { line, area, min, max, changePct, up, lastTime: candles[candles.length - 1][0] }
  }, [candles])

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-slate-300">{t('chart.last24h')}</h3>
        {chart && (
          <span
            className={`text-sm font-medium ${
              chart.changePct === null ? 'text-slate-500' : chart.up ? 'text-emerald-400' : 'text-red-400'
            }`}
          >
            {fmtPct(chart.changePct)}
          </span>
        )}
      </div>

      {error ? (
        <p className="py-10 text-center text-sm text-slate-500">{t('chart.loadError')}</p>
      ) : !chart ? (
        <div className="h-40 animate-pulse rounded-md bg-slate-800/40" />
      ) : (
        <>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="h-40 w-full"
            role="img"
            aria-label={`${market} price over the last 24 hours`}
          >
            <defs>
              <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={chart.up ? '#34d399' : '#f87171'} stopOpacity="0.25" />
                <stop offset="100%" stopColor={chart.up ? '#34d399' : '#f87171'} stopOpacity="0" />
              </linearGradient>
            </defs>
            <polygon points={chart.area} fill="url(#chartFill)" />
            <polyline
              points={chart.line}
              fill="none"
              stroke={chart.up ? '#34d399' : '#f87171'}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <div className="mt-2 flex justify-between text-xs text-slate-500">
            <span>
              {t('chart.low')} {fmtPrice(chart.min)}
            </span>
            <span>
              {t('chart.high')} {fmtPrice(chart.max)}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
