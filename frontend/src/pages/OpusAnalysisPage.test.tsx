import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../i18n'
import { api } from '../lib/api'
import type { Market, OpusAnalysis, OpusRankingRow, OpusRankings } from '../lib/types'
import OpusAnalysisPage from './OpusAnalysisPage'

vi.mock('../lib/api', () => ({ api: vi.fn() }))
vi.mock('../lib/usePrices', () => ({
  usePrices: () => ({ prices: { 'BTC-EUR': { last: '64250.12' } } }),
}))

const mockedApi = vi.mocked(api)

const market: Market = {
  market: 'BTC-EUR',
  base: 'BTC',
  quote: 'EUR',
  name: 'Bitcoin',
  listing: null,
  asset_class: 'crypto',
  market_open: true,
  last: '64000',
  bid: '63990',
  ask: '64010',
  open: '63000',
  change_24h_pct: '1.5',
  volume_quote: '1000000',
  has_news: true,
}

function row(overrides: Partial<OpusRankingRow> = {}): OpusRankingRow {
  return {
    market: 'BTC-EUR',
    name: 'Bitcoin',
    asset_class: 'crypto',
    peer_group: 'crypto',
    regime: 'up',
    day: '2026-08-02T00:00:00Z',
    days_since_close: 0,
    close: '64000',
    confidence: 'medium',
    weights_learned: true,
    turnover_eur: 5_000_000,
    corr_mkt: 0.8,
    liquidity_ok: true,
    stale: false,
    tradable: true,
    tradable_now: true,
    suggested_order_type: 'market',
    held: false,
    taker_pct: '0.25',
    maker_pct: '0.15',
    buy_rank: 1,
    sell_rank: 400,
    action: 'buy',
    score: 42,
    direction: 'bullish',
    expected_return_pct: '1.80',
    fee_pct: '0.50',
    limit_fee_pct: '0.30',
    net_edge_pct: '1.30',
    net_edge_limit_pct: '1.50',
    sell_edge_pct: '-2.05',
    conviction: '0.42',
    buy_score: 61,
    sell_score: 0,
    low_volatility: false,
    requires_limit_order: false,
    tradable_edge: true,
    horizon: '1w',
    horizon_bars: 5,
    expected_move_pct: '3.58',
    market_return_pct: '0.40',
    alpha_pct: '1.40',
    suggested_stop_pct: '5.20',
    suggested_stop_price: '60672',
    ...overrides,
  }
}

function rankings(overrides: Partial<OpusRankings> = {}): OpusRankings {
  return {
    generated_at: '2026-08-03T10:00:00Z',
    engine_version: 'opus-1',
    horizon: '1w',
    side: 'buy',
    regimes: { crypto: 'up', stock: 'down', other: 'all' },
    group_days: { crypto: '2026-08-02T00:00:00Z' },
    macro: {
      vix: 14.2,
      vix_day: '2026-08-01',
      us10y: 4.1,
      us2y: 3.9,
      yield_curve: 0.2,
      fear_greed: 61,
      fear_greed_day: '2026-08-02',
      stablecoin_change_30d_pct: 1.4,
    },
    calibrated: true,
    markets: 561,
    basket: ['BTC-EUR'],
    rankings: [row(), row({ market: 'SOL-EUR', name: 'Solana', buy_rank: 2, action: 'hold', buy_score: 12, net_edge_pct: '-0.10', tradable: true })],
    ...overrides,
  }
}

function analysis(overrides: Partial<OpusAnalysis> = {}): OpusAnalysis {
  return {
    market: 'BTC-EUR',
    range: '30d',
    horizon: '1w',
    generated_at: '2026-08-03T10:00:00Z',
    candles: [
      [1754000000000, '60000', '61000', '59000', '60500', '100'],
      [1754086400000, '60500', '65000', '60000', '64000', '120'],
    ],
    mode: 'cross_sectional',
    outlook: {
      direction: 'bullish',
      score: 42,
      buy_score: 61,
      sell_score: 0,
      confidence: 'medium',
      regime: 'up',
      reason: { code: 'outlook_bullish', params: { bullish: 9, bearish: 4, neutral: 2, total: 15 } },
      contributions: [{ strategy: 'mom_21', signal: 'bullish', weight: 12.4 }],
    },
    strategies: {
      mom_21: {
        signal: 'bullish',
        reason: { code: 'opus_percentile_top', params: { percentile: 93 } },
        explanation: 'Risk-adjusted momentum over 21 days.',
        values: { z: '1.62', percentile: '93', weight: '12.40', contribution: '4.10', ic: '1.80', ic_days: '900' },
        series: {},
      },
      rev_5: {
        signal: 'neutral',
        reason: { code: 'opus_percentile_mid', params: { percentile: 48 } },
        explanation: 'Five-day reversal.',
        values: { z: '-0.04', percentile: '48', weight: '3.10', contribution: '-0.10' },
        series: {},
      },
    },
    recommendation: {
      action: 'buy',
      score: 42,
      direction: 'bullish',
      expected_return_pct: '1.80',
      fee_pct: '0.50',
      limit_fee_pct: '0.30',
      net_edge_pct: '1.30',
      net_edge_limit_pct: '1.50',
      sell_edge_pct: '-2.05',
      conviction: '0.42',
      buy_score: 61,
      sell_score: 0,
      low_volatility: false,
      requires_limit_order: false,
      tradable_edge: true,
      horizon: '1w',
      horizon_bars: 5,
      expected_move_pct: '3.58',
      market_return_pct: '0.40',
      alpha_pct: '1.40',
      suggested_stop_pct: '5.20',
      suggested_stop_price: '60672',
    },
    calibration: {
      engine_version: 'opus-1',
      peer_group: 'crypto',
      horizon: '1w',
      regime: 'up',
      weights_learned: true,
      days: 1200,
      from: '2021-02-01',
      to: '2026-08-02',
      calibrated_at: '2026-08-03T04:00:00Z',
      walk_forward_ic: '1.80',
      walk_forward_ic_days: 900,
      walk_forward_hit_rate_pct: '53.2',
      walk_forward_samples: 59227,
      market_return_pct: '0.40',
      market_return_std_pct: '5.10',
      top_features: [{ feature: 'mom_21', weight: '12.40' }],
    },
    cross_section: {
      peer_group: 'crypto',
      peers: 430,
      regime: 'up',
      day: '2026-08-02T00:00:00Z',
      days_since_close: 0,
    },
    gates: {
      liquidity_ok: true,
      stale: false,
      tradable: true,
      tradable_now: true,
      low_volatility: false,
      suggested_order_type: 'market',
      turnover_eur: '5000000',
    },
    macro: rankings().macro,
    track_record: {
      hit_rate_pct: '52.4',
      samples: 210,
      forward_days: 5,
      avg_bullish_return_pct: '0.90',
      avg_bearish_return_pct: '-0.60',
      from: '2021-03-01',
      to: '2026-08-01',
    },
    live_track_record: null,
    live_track_record_all: {
      hit_rate_pct: '55.0',
      samples: 40,
      horizon: '1w',
      buy_samples: 25,
      sell_samples: 15,
      avg_buy_return_pct: '1.10',
      avg_sell_return_pct: '-0.80',
      from: '2026-07-01',
      to: '2026-08-01',
    },
    ...overrides,
  }
}

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/opus-analysis" element={<OpusAnalysisPage />} />
        <Route path="/opus-analysis/:market" element={<OpusAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(async () => {
  mockedApi.mockReset()
  await i18n.changeLanguage('en')
})

describe('Opus ranking board', () => {
  it('lists ranked markets with edge, action and the diversified basket', async () => {
    mockedApi.mockResolvedValue(rankings())
    renderPage('/opus-analysis')

    await waitFor(() => expect(screen.getByText('561 markets scored, updated 03/08/26, 12:00 · Crypto: peer index trending up · Stocks: peer index trending down · Funds & commodities: no clear peer trend')).toBeInTheDocument())
    expect(screen.getByText('Diversified basket')).toBeInTheDocument()
    expect(screen.getAllByText('+1.30%').length).toBeGreaterThan(0)
    expect(screen.getByText('Buy')).toBeInTheDocument()
    expect(screen.getByText('Hold')).toBeInTheDocument()
    expect(mockedApi).toHaveBeenCalledWith('/markets/opus-rankings?horizon=1w&side=buy&limit=600')
  })

  it('refetches for another horizon and the sell side', async () => {
    mockedApi.mockResolvedValue(rankings())
    renderPage('/opus-analysis')
    await waitFor(() => expect(screen.getByText('Diversified basket')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '4 weeks' }))
    await waitFor(() =>
      expect(mockedApi).toHaveBeenCalledWith('/markets/opus-rankings?horizon=4w&side=buy&limit=600'),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Best sells' }))
    await waitFor(() =>
      expect(mockedApi).toHaveBeenCalledWith('/markets/opus-rankings?horizon=4w&side=sell&limit=600'),
    )
  })

  it('explains an empty basket instead of showing nothing', async () => {
    mockedApi.mockResolvedValue(rankings({ basket: [] }))
    renderPage('/opus-analysis')
    await waitFor(() =>
      expect(
        screen.getByText('No market currently offers an edge that survives fees on this horizon.'),
      ).toBeInTheDocument(),
    )
  })

  it('shows the load error when the ranking cannot be fetched', async () => {
    mockedApi.mockRejectedValue(new Error('boom'))
    renderPage('/opus-analysis')
    await waitFor(() =>
      expect(screen.getByText('Could not load the Opus ranking')).toBeInTheDocument(),
    )
  })
})

describe('Opus detail view', () => {
  function mockDetail(data: OpusAnalysis = analysis()) {
    mockedApi.mockImplementation((path: string) => {
      if (path === '/markets') return Promise.resolve([market] as never)
      return Promise.resolve(data as never)
    })
  }

  it('shows the verdict, the euro numbers and the calibration provenance', async () => {
    mockDetail()
    renderPage('/opus-analysis/BTC-EUR')

    await waitFor(() => expect(screen.getByText('Opus analysis: BTC-EUR')).toBeInTheDocument())
    expect(screen.getByText('Leaning buy')).toBeInTheDocument() // gauge zone for +42
    expect(screen.getByText('Buy')).toBeInTheDocument()
    expect(screen.getByText('9 of 15 weighted features rank this above its peers.')).toBeInTheDocument()
    expect(screen.getByText('Ranked against 430 markets in Crypto · Features from the completed session of 2026-08-02')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Weights learned for Crypto on the 1 week horizon, peer index trending up, from 1200 days of history (2021-02-01 → 2026-08-02).',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Across all markets')).toBeInTheDocument()
  })

  it('expands the feature table with percentile, weight and contribution', async () => {
    mockDetail()
    renderPage('/opus-analysis/BTC-EUR')
    await waitFor(() => expect(screen.getByText('Opus analysis: BTC-EUR')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Show all 2 features' }))
    // Also listed as a heaviest-weight chip in the calibration card.
    expect(screen.getAllByText('Momentum (21 days, risk-adjusted)')).toHaveLength(2)
    expect(
      screen.getByText('Percentile 93 within its peer group — among the highest readings today.'),
    ).toBeInTheDocument()
    expect(screen.getByTitle('Five-day reversal.')).toBeInTheDocument()
    expect(screen.getByText('93')).toBeInTheDocument()
  })

  it('falls back to a plain explanation when there is no calibration yet', async () => {
    mockDetail(analysis({ calibration: null, mode: 'time_series' }))
    renderPage('/opus-analysis/BTC-EUR')
    await waitFor(() =>
      expect(
        screen.getByText(
          'No reliable calibration for this segment yet, so documented prior weights are in use and no expected return is shown.',
        ),
      ).toBeInTheDocument(),
    )
    expect(
      screen.getByText(
        'No peer cross-section available for this market — features are ranked against its own past year, without a calibrated expected return.',
      ),
    ).toBeInTheDocument()
  })

  it('requests the selected horizon and range', async () => {
    mockDetail()
    renderPage('/opus-analysis/BTC-EUR')
    await waitFor(() =>
      expect(mockedApi).toHaveBeenCalledWith('/markets/BTC-EUR/opus-analysis?range=30d&horizon=1w'),
    )

    await userEvent.click(screen.getByRole('button', { name: '1 day' }))
    await waitFor(() =>
      expect(mockedApi).toHaveBeenCalledWith('/markets/BTC-EUR/opus-analysis?range=30d&horizon=1d'),
    )
  })
})
