import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { IChartApi, LogicalRange } from 'lightweight-charts'
import { api } from './api'
import type { Candle } from './types'

/** Pages of older bars a chart may add on top of its preset window. */
export const MAX_HISTORY_PAGES = 9

/** Fetch the next page once the viewport comes this close to the left edge. */
export const LOAD_THRESHOLD_BARS = 10

/** Bars within this many logical units of a perfect fit still count as fitted. */
const FIT_TOLERANCE = 1

export async function fetchCandlePage(
  market: string,
  range: string,
  end?: number,
  signal?: AbortSignal,
): Promise<Candle[]> {
  const params = new URLSearchParams({ range })
  if (end !== undefined) params.set('end', String(end))
  return api<Candle[]>(`/markets/${encodeURIComponent(market)}/candles?${params}`, { signal })
}

/** The API bound is exclusive; filter anyway so a stray bar cannot duplicate. */
export function olderThan(page: Candle[], ts: number): Candle[] {
  return page.filter((c) => c[0] < ts)
}

/**
 * Whether the chart should keep auto-following the newest bar. True only while
 * it is still in the fitted state: no extra history and a viewport that spans
 * exactly the dataset. Zooming in raises `from` and lowers `to`; zooming out
 * pushes both past the ends — either way the viewport is worth preserving.
 */
export function shouldFitContent(
  olderCount: number,
  logicalRange: { from: number; to: number } | null,
  barCount: number,
): boolean {
  if (olderCount > 0) return false
  if (logicalRange === null) return true
  return (
    Math.abs(logicalRange.from) <= FIT_TOLERANCE &&
    Math.abs(logicalRange.to - (barCount - 1)) <= FIT_TOLERANCE
  )
}

/**
 * Accumulates pages of older bars in front of a chart's preset window.
 *
 * The caller owns `baseBars` (its own fetch or payload); this hook only ever
 * adds history in front of them, and throws its pages away when the market or
 * range changes.
 */
export function useOlderHistory({
  market,
  range,
  baseBars,
}: {
  market: string
  range: string
  baseBars: Candle[] | null
}) {
  const [older, setOlder] = useState<Candle[]>([])
  const [pages, setPages] = useState(0)
  const [exhausted, setExhausted] = useState(false)
  const [loading, setLoading] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef(false)
  const oldestRef = useRef<number | null>(null)

  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    inFlightRef.current = false
    setOlder([])
    setPages(0)
    setExhausted(false)
    setLoading(false)
  }, [market, range])

  useEffect(() => () => abortRef.current?.abort(), [])

  const bars = useMemo(() => {
    const merged = baseBars && baseBars.length > 0 ? [...older, ...baseBars] : older
    oldestRef.current = merged.length > 0 ? merged[0][0] : null
    return merged
  }, [older, baseBars])

  const canLoadMore = !exhausted && pages < MAX_HISTORY_PAGES && bars.length > 0

  const loadOlder = useCallback(() => {
    if (inFlightRef.current || exhausted || pages >= MAX_HISTORY_PAGES) return
    const oldest = oldestRef.current
    if (oldest === null) return

    inFlightRef.current = true
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller

    fetchCandlePage(market, range, oldest, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return
        const fresh = olderThan(page, oldest)
        if (fresh.length === 0) {
          setExhausted(true)
          return
        }
        setPages((n) => n + 1)
        setOlder((current) => [...fresh, ...current])
      })
      .catch(() => {
        // Keep what is loaded; the next viewport change retries.
      })
      .finally(() => {
        if (controller.signal.aborted) return
        inFlightRef.current = false
        setLoading(false)
      })
  }, [market, range, exhausted, pages])

  return { bars, olderCount: older.length, loadOlder, loading, canLoadMore }
}

/**
 * Call `loadOlder` when the viewport nears the left edge of the loaded bars.
 * Returns an unsubscribe function.
 */
export function attachHistoryTrigger(
  chart: IChartApi,
  canLoad: () => boolean,
  loadOlder: () => void,
): () => void {
  const handler = (logicalRange: LogicalRange | null) => {
    if (logicalRange === null) return
    if (logicalRange.from > LOAD_THRESHOLD_BARS) return
    if (!canLoad()) return
    loadOlder()
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(handler)
  return () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler)
}
