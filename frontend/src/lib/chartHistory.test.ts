import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import type { Candle } from './types'
import {
  MAX_HISTORY_PAGES,
  olderThan,
  shouldFitContent,
  useOlderHistory,
} from './chartHistory'

vi.mock('./api', () => ({ api: vi.fn() }))

const mockedApi = vi.mocked(api)

const MINUTE = 60_000

/** `count` bars ending just before `end`, one minute apart. */
function page(end: number, count = 3): Candle[] {
  return Array.from({ length: count }, (_, i) => {
    const ts = end - (count - i) * MINUTE
    return [ts, '1', '1', '1', '1', '1'] as Candle
  })
}

const BASE_END = 1_000 * MINUTE
const baseBars = page(BASE_END, 3)

beforeEach(() => {
  mockedApi.mockReset()
})

describe('olderThan', () => {
  it('keeps only bars strictly older than the bound', () => {
    const bars = page(BASE_END, 3)
    expect(olderThan(bars, bars[1][0])).toEqual([bars[0]])
  })

  it('drops everything at or after the bound', () => {
    const bars = page(BASE_END, 3)
    expect(olderThan(bars, bars[0][0])).toEqual([])
  })
})

describe('shouldFitContent', () => {
  it('fits while the viewport matches the full dataset', () => {
    expect(shouldFitContent(0, { from: 0, to: 99 }, 100)).toBe(true)
  })

  it('fits when there is no viewport yet', () => {
    expect(shouldFitContent(0, null, 100)).toBe(true)
  })

  it('preserves the viewport when zoomed in', () => {
    expect(shouldFitContent(0, { from: 40, to: 60 }, 100)).toBe(false)
  })

  it('preserves the viewport when zoomed out past both ends', () => {
    expect(shouldFitContent(0, { from: -50, to: 150 }, 100)).toBe(false)
  })

  it('preserves the viewport once older pages are loaded', () => {
    expect(shouldFitContent(1, { from: 0, to: 99 }, 100)).toBe(false)
  })
})

describe('useOlderHistory', () => {
  it('returns the base bars untouched before any paging', () => {
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))
    expect(result.current.bars).toEqual(baseBars)
    expect(result.current.olderCount).toBe(0)
    expect(mockedApi).not.toHaveBeenCalled()
  })

  it('requests the page before the oldest loaded bar', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())

    await waitFor(() => expect(result.current.olderCount).toBe(3))
    expect(mockedApi.mock.calls[0][0]).toBe(
      `/markets/BTC-EUR/candles?range=1d&end=${baseBars[0][0]}`,
    )
    expect(result.current.bars).toHaveLength(6)
    expect(result.current.bars[0][0]).toBeLessThan(baseBars[0][0])
    expect(result.current.bars.at(-1)).toEqual(baseBars.at(-1))
  })

  it('stops asking once a page comes back empty', async () => {
    mockedApi.mockResolvedValue([])
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.canLoadMore).toBe(false))

    act(() => result.current.loadOlder())
    expect(mockedApi).toHaveBeenCalledTimes(1)
  })

  it('issues one request when triggered twice in a row', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => {
      result.current.loadOlder()
      result.current.loadOlder()
    })

    await waitFor(() => expect(result.current.olderCount).toBe(3))
    expect(mockedApi).toHaveBeenCalledTimes(1)
  })

  it('stops at the page cap', async () => {
    mockedApi.mockImplementation((path: string) => {
      const end = Number(new URL(path, 'http://x').searchParams.get('end'))
      return Promise.resolve(page(end, 3))
    })
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    for (let i = 0; i < MAX_HISTORY_PAGES + 3; i++) {
      act(() => result.current.loadOlder())
      await waitFor(() => expect(result.current.loading).toBe(false))
    }

    expect(mockedApi).toHaveBeenCalledTimes(MAX_HISTORY_PAGES)
    expect(result.current.canLoadMore).toBe(false)
    expect(result.current.olderCount).toBe(MAX_HISTORY_PAGES * 3)
  })

  it('discards loaded pages when the range changes', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result, rerender } = renderHook(
      ({ range }) => useOlderHistory({ market: 'BTC-EUR', range, baseBars }),
      { initialProps: { range: '1d' } },
    )

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.olderCount).toBe(3))

    rerender({ range: '1w' })
    expect(result.current.olderCount).toBe(0)
    expect(result.current.bars).toEqual(baseBars)
  })

  it('keeps the loaded pages when a page request fails', async () => {
    mockedApi.mockRejectedValue(new Error('502'))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.bars).toEqual(baseBars)
    expect(result.current.canLoadMore).toBe(true)
  })
})
