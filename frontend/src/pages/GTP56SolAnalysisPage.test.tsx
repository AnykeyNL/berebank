import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../i18n'
import DirectionOutlookCard from '../components/DirectionOutlookCard'
import { api } from '../lib/api'
import { largestRemainderPercentages } from '../lib/gtp56sol'
import type { GTP56SolAnalysis, Market } from '../lib/types'
import GTP56SolAnalysisPage from './GTP56SolAnalysisPage'

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

function forecast(overrides: Partial<GTP56SolAnalysis> = {}): GTP56SolAnalysis {
  return {
    market: 'BTC-EUR',
    asset_class: 'crypto',
    generated_at: '2026-08-02T12:00:00Z',
    status: 'ok',
    horizon: '1d',
    source_scope: 'asset',
    probabilities: { up: '0.5149', sideways: '0.2451', down: '0.24' },
    direction: 'bullish',
    confidence: 'medium',
    drivers: [
      { code: 'historical_probability_leader', params: { outcome: 'up', probability: '0.5149' } },
      { code: 'technical_vote_balance', params: { balance: '2' } },
      { code: 'walk_forward_evidence', params: { evaluated_samples: 24, directional_accuracy: '0.625' } },
    ],
    sample_count: 80,
    effective_sample_count: 31,
    candidate_pool_size: 220,
    average_similarity: '0.62',
    validation: {
      evaluated_samples: 24,
      effective_evaluated_samples: 18,
      directional_accuracy: '0.625',
      majority_baseline_accuracy: '0.5',
      period_start: '2023-01-01T00:00:00Z',
      period_end: '2026-01-01T00:00:00Z',
    },
    period_start: '2021-01-01T00:00:00Z',
    period_end: '2026-08-01T00:00:00Z',
    evidence_period_start: '2021-03-01T00:00:00Z',
    evidence_period_end: '2026-07-31T00:00:00Z',
    ...overrides,
  }
}

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/gtp56sol-analysis" element={<GTP56SolAnalysisPage />} />
        <Route path="/gtp56sol-analysis/:market" element={<GTP56SolAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(async () => {
  mockedApi.mockReset()
  await i18n.changeLanguage('en')
})

describe('largestRemainderPercentages', () => {
  it.each([
    [{ up: '0.3333', sideways: '0.3333', down: '0.3334' }, [33, 33, 34]],
    [{ up: '0.5149', sideways: '0.2451', down: '0.24' }, [51, 25, 24]],
    [{ up: '0.005', sideways: '0.005', down: '0.99' }, [1, 0, 99]],
  ])('rounds tricky decimal strings to exactly 100', (values, expected) => {
    const rounded = largestRemainderPercentages(values)
    expect(rounded).not.toBeNull()
    if (!rounded) throw new Error('expected valid percentages')
    expect([rounded.up, rounded.sideways, rounded.down]).toEqual(expected)
    expect(Object.values(rounded).reduce((sum, value) => sum + value, 0)).toBe(100)
  })

  it.each([
    [{ up: 'NaN', sideways: '0.5', down: '0.5' }],
    [{ up: '-0.1', sideways: '0.5', down: '0.6' }],
    [{ up: '0', sideways: '0', down: '0' }],
    [{ up: 'Infinity', sideways: '0', down: '0' }],
    [{ up: '0.5', sideways: '0.5' }],
  ])('rejects malformed probability sets', (values) => {
    expect(largestRemainderPercentages(values)).toBeNull()
  })
})

describe('DirectionOutlookCard', () => {
  it('shows plain direction, confidence, probabilities, drivers, and compact evidence', () => {
    render(<DirectionOutlookCard title="Next session" bars={1} result={forecast()} />)

    expect(screen.getByRole('heading', { name: /Next session/ })).toBeInTheDocument()
    expect(screen.getByText('Likely up')).toBeInTheDocument()
    expect(screen.getByText('Confidence: Medium')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Outcome probabilities' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Up 51%' })).toHaveAttribute('aria-valuenow', '51')
    expect(screen.getByRole('progressbar', { name: 'Sideways 25%' })).toHaveAttribute('aria-valuenow', '25')
    expect(screen.getByRole('progressbar', { name: 'Down 24%' })).toHaveAttribute('aria-valuenow', '24')
    expect(screen.getAllByRole('progressbar')).toHaveLength(3)
    expect(screen.getByText(/80 raw \/ 31 effective/)).toBeInTheDocument()
    expect(screen.getByText(/62% average similarity/)).toBeInTheDocument()
    expect(screen.getByText(/63% vs 50% baseline/)).toBeInTheDocument()
    expect(screen.getByText(/Similar historical setups most often moved up/)).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('uses displayed largest-remainder percentage in the leading driver', () => {
    render(
      <DirectionOutlookCard
        title="Next session"
        bars={1}
        result={forecast({
          probabilities: { up: '0.3349', sideways: '0.333', down: '0.3321' },
          drivers: [
            {
              code: 'historical_probability_leader',
              params: { outcome: 'up', probability: '0.3349' },
            },
          ],
        })}
      />,
    )

    expect(screen.getByText(/most often moved up \(34%\)/)).toBeInTheDocument()
  })

  it('renders insufficient history without fabricated probabilities', () => {
    render(
      <DirectionOutlookCard
        title="Next month"
        bars={21}
        result={forecast({
          status: 'insufficient_history',
          probabilities: null,
          drivers: [],
          sample_count: 0,
          effective_sample_count: 0,
          average_similarity: null,
        })}
      />,
    )

    expect(screen.getByText('Not enough completed history yet')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('discloses same-class fallback and expands methodology accessibly', async () => {
    const user = userEvent.setup()
    render(
      <DirectionOutlookCard
        title="Next week"
        bars={5}
        result={forecast({ source_scope: 'asset_class', horizon: '1w' })}
      />,
    )

    expect(screen.getByText(/same-class fallback/)).toBeInTheDocument()
    const disclosure = screen.getByRole('button', { name: 'Show methodology and evidence' })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    const details = document.getElementById(disclosure.getAttribute('aria-controls')!)
    expect(details).toBeInTheDocument()
    expect(details).toHaveAttribute('hidden')
    await user.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(details).not.toHaveAttribute('hidden')
    expect(screen.getByText(/nearest historical setups/)).toBeInTheDocument()
  })

  it('keeps a prior forecast visible when refresh fails', () => {
    render(
      <DirectionOutlookCard
        title="Next session"
        bars={1}
        result={forecast()}
        error
      />,
    )

    expect(screen.getByText('Likely up')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Refresh failed. Showing the previous forecast.',
    )
    expect(screen.queryByText('Could not load this horizon')).not.toBeInTheDocument()
  })

  it('shows retry for an initial error and invokes only its callback', async () => {
    const retry = vi.fn()
    const user = userEvent.setup()
    render(
      <DirectionOutlookCard
        title="Next week"
        bars={5}
        result={null}
        error
        onRetry={retry}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Retry Next week' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('renders malformed runtime values with friendly safe defaults', () => {
    const malformed = {
      ...forecast(),
      direction: 'rocket',
      confidence: 'certain',
      source_scope: 'internet',
      drivers: [
        { code: 'unknown_driver', params: { outcome: 'moon', probability: '0.5149' } },
      ],
    } as unknown as GTP56SolAnalysis

    render(<DirectionOutlookCard title="Next session" bars={1} result={malformed} />)

    expect(screen.getByText('No clear direction')).toBeInTheDocument()
    expect(screen.getByText('Confidence: Low')).toBeInTheDocument()
    expect(screen.getByText(/source unavailable/)).toBeInTheDocument()
    expect(screen.getByText('Additional historical evidence is unavailable.')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/gtp56solAnalysis\./)
  })

  it('treats ok with null or invalid probabilities as unavailable', () => {
    const { rerender } = render(
      <DirectionOutlookCard
        title="Next session"
        bars={1}
        result={forecast({ probabilities: null })}
      />,
    )
    expect(screen.getByText('Forecast probabilities are unavailable')).toBeInTheDocument()

    rerender(
      <DirectionOutlookCard
        title="Next session"
        bars={1}
        result={forecast({
          probabilities: { up: 'NaN', sideways: '0.4', down: '0.6' },
        })}
      />,
    )
    expect(screen.getByText('Forecast probabilities are unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('maps an unknown runtime status to a friendly unavailable state', () => {
    render(
      <DirectionOutlookCard
        title="Next session"
        bars={1}
        result={{ ...forecast(), status: 'mystery' } as unknown as GTP56SolAnalysis}
      />,
    )

    expect(screen.getByText('This forecast is currently unavailable')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('gtp56solAnalysis.states.mystery')
  })
})

describe('GTP56SolAnalysisPage', () => {
  it('renders successful horizons when one horizon request fails', async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout')
    mockedApi.mockImplementation((path) => {
      if (path === '/markets') return Promise.resolve([market])
      if (path.includes('horizon=1d')) return Promise.resolve(forecast())
      if (path.includes('horizon=1w')) return Promise.reject(new Error('failed'))
      if (path.includes('horizon=1m')) {
        return Promise.resolve(forecast({ horizon: '1m', direction: 'neutral', confidence: 'low' }))
      }
      return Promise.reject(new Error(`Unexpected path: ${path}`))
    })

    renderPage('/gtp56sol-analysis/BTC-EUR')

    expect(await screen.findByText('Likely up')).toBeInTheDocument()
    expect(await screen.findByText('No clear direction')).toBeInTheDocument()
    expect(screen.getByText('Could not load this horizon')).toBeInTheDocument()
    expect(mockedApi).toHaveBeenCalledWith(
      '/markets/BTC-EUR/gtp56sol-analysis?horizon=1d',
      { signal: expect.any(AbortSignal) },
    )
    expect(mockedApi).toHaveBeenCalledWith(
      '/markets/BTC-EUR/gtp56sol-analysis?horizon=1w',
      { signal: expect.any(AbortSignal) },
    )
    expect(mockedApi).toHaveBeenCalledWith(
      '/markets/BTC-EUR/gtp56sol-analysis?horizon=1m',
      { signal: expect.any(AbortSignal) },
    )
    expect(timeoutSpy).toHaveBeenCalledWith(60_000)
    timeoutSpy.mockRestore()
  })

  it('uses only the market list on the asset picker', async () => {
    mockedApi.mockImplementation((path) => {
      if (path === '/markets') return Promise.resolve([market])
      if (path === '/markets/gtp56sol-outlooks?horizon=1w') {
        return Promise.resolve({
          generated_at: '2026-08-02T12:00:00Z',
          horizon: '1w',
          outlooks: {},
        })
      }
      return Promise.reject(new Error(`Unexpected path: ${path}`))
    })
    renderPage('/gtp56sol-analysis')

    expect(await screen.findByRole('button', { name: /BTC/ })).toBeInTheDocument()
    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith('/markets'))
    expect(mockedApi).toHaveBeenCalledWith('/markets/gtp56sol-outlooks?horizon=1w', {
      signal: expect.any(AbortSignal),
    })
    expect(mockedApi.mock.calls.some(([path]) => String(path).includes('gtp56sol-analysis?horizon='))).toBe(false)
  })

  it('sorts picker rows by net score descending by default', async () => {
    const eth: Market = { ...market, market: 'ETH-EUR', base: 'ETH', name: 'Ethereum' }
    mockedApi.mockImplementation((path) => {
      if (path === '/markets') return Promise.resolve([market, eth])
      if (path === '/markets/gtp56sol-outlooks?horizon=1w') {
        return Promise.resolve({
          generated_at: '2026-08-02T12:00:00Z',
          horizon: '1w',
          outlooks: {
            'BTC-EUR': { direction: 'bullish', score: 25, confidence: 'medium' },
            'ETH-EUR': { direction: 'bearish', score: -10, confidence: 'low' },
          },
        })
      }
      return Promise.reject(new Error(`Unexpected path: ${path}`))
    })

    renderPage('/gtp56sol-analysis')
    const rows = await screen.findAllByRole('button', { name: /Bitcoin|Ethereum/ })
    expect(rows[0]).toHaveAccessibleName(/Bitcoin/)
    expect(rows[1]).toHaveAccessibleName(/Ethereum/)
    expect(screen.getByText('+25')).toBeInTheDocument()
    expect(screen.getByText('-10')).toBeInTheDocument()
  })

  it('announces picker loading and a successful empty market list', async () => {
    let resolveMarkets: (markets: Market[]) => void = () => {}
    mockedApi.mockImplementation((path) => {
      if (path === '/markets') {
        return new Promise<Market[]>((resolve) => {
          resolveMarkets = resolve
        })
      }
      if (path === '/markets/gtp56sol-outlooks?horizon=1w') {
        return Promise.resolve({
          generated_at: '2026-08-02T12:00:00Z',
          horizon: '1w',
          outlooks: {},
        })
      }
      return Promise.reject(new Error(`Unexpected path: ${path}`))
    })
    renderPage('/gtp56sol-analysis')

    expect(screen.getByRole('status')).toHaveTextContent('Loading assets…')
    resolveMarkets([])
    expect(await screen.findByText('No assets are currently available.')).toBeInTheDocument()
  })
})
