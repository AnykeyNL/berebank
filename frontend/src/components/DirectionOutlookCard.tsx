import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fmtDateTime } from '../lib/format'
import { largestRemainderPercentages } from '../lib/gtp56sol'
import type { GTP56SolAnalysis } from '../lib/types'

const OUTCOMES = ['up', 'sideways', 'down'] as const
type DisplayedProbabilities = Record<(typeof OUTCOMES)[number], number>

function percent(value: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${Math.round(parsed * 100)}%` : '—'
}

function statusKey(value: unknown): 'ok' | 'insufficient_history' | 'stale' | 'unavailable' {
  if (value === 'ok' || value === 'insufficient_history' || value === 'stale' || value === 'unavailable') {
    return value
  }
  return 'unavailable'
}

function directionKey(value: unknown): 'bullish' | 'bearish' | 'neutral' {
  return value === 'bullish' || value === 'bearish' ? value : 'neutral'
}

function confidenceKey(value: unknown): 'low' | 'medium' | 'high' {
  return value === 'medium' || value === 'high' ? value : 'low'
}

function sourceScopeKey(value: unknown): 'asset' | 'asset_class' | 'unknown' {
  return value === 'asset' || value === 'asset_class' ? value : 'unknown'
}

function outcomeKey(value: unknown): (typeof OUTCOMES)[number] | null {
  return value === 'up' || value === 'sideways' || value === 'down' ? value : null
}

function outcomeLabel(
  outcome: (typeof OUTCOMES)[number],
  t: (key: string) => string,
): string {
  if (outcome === 'up') return t('gtp56solAnalysis.probabilities.up')
  if (outcome === 'down') return t('gtp56solAnalysis.probabilities.down')
  return t('gtp56solAnalysis.probabilities.sideways')
}

function driverText(
  driver: unknown,
  displayed: DisplayedProbabilities,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  if (!driver || typeof driver !== 'object') {
    return t('gtp56solAnalysis.drivers.unavailable')
  }
  const record = driver as Record<string, unknown>
  const params = record.params && typeof record.params === 'object'
    ? record.params as Record<string, unknown>
    : {}

  switch (record.code) {
    case 'historical_probability_leader': {
      const outcome = outcomeKey(params.outcome)
      if (!outcome) return t('gtp56solAnalysis.drivers.unavailable')
      return t('gtp56solAnalysis.drivers.historical_probability_leader', {
        outcome: outcomeLabel(outcome, t).toLocaleLowerCase(),
        probability: `${displayed[outcome]}%`,
      })
    }
    case 'technical_vote_balance': {
      const balance = Number(params.balance)
      if (!Number.isFinite(balance)) return t('gtp56solAnalysis.drivers.unavailable')
      const value = Math.abs(balance)
      let vote: string
      if (balance === 0) {
        vote = t('gtp56solAnalysis.drivers.voteBalanced')
      } else if (balance > 0 && value === 1) {
        vote = t('gtp56solAnalysis.drivers.voteUpOne', { value })
      } else if (balance > 0) {
        vote = t('gtp56solAnalysis.drivers.voteUpMany', { value })
      } else if (value === 1) {
        vote = t('gtp56solAnalysis.drivers.voteDownOne', { value })
      } else {
        vote = t('gtp56solAnalysis.drivers.voteDownMany', { value })
      }
      return t('gtp56solAnalysis.drivers.technical_vote_balance', { balance: vote })
    }
    case 'walk_forward_evidence':
      return t('gtp56solAnalysis.drivers.walk_forward_evidence', {
        directional_accuracy: percent(params.directional_accuracy),
        evaluated_samples:
          typeof params.evaluated_samples === 'number' ? params.evaluated_samples : 0,
      })
    default:
      return t('gtp56solAnalysis.drivers.unavailable')
  }
}

export default function DirectionOutlookCard({
  title,
  bars,
  result,
  error = false,
  onRetry,
}: {
  title: string
  bars: number
  result: GTP56SolAnalysis | null
  error?: boolean
  onRetry?: () => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()

  if (error && !result) {
    return (
      <article className="rounded-xl border border-red-900/50 bg-slate-900/60 p-4">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p role="alert" className="mt-3 text-sm text-red-300">
          {t('gtp56solAnalysis.states.error')}
        </p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            aria-label={t('gtp56solAnalysis.states.retryLabel', { title })}
            className="mt-3 rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
          >
            {t('gtp56solAnalysis.states.retry')}
          </button>
        )}
      </article>
    )
  }

  if (!result) {
    return (
      <article className="min-h-56 animate-pulse rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="text-lg font-semibold">{title}</h2>
        <span role="status" className="sr-only">{t('gtp56solAnalysis.states.loading')}</span>
      </article>
    )
  }

  const status = statusKey(result.status)
  const displayed = status === 'ok'
    ? largestRemainderPercentages(result.probabilities)
    : null
  if (status !== 'ok' || !displayed) {
    const state = status === 'ok' ? 'probabilitiesUnavailable' : status
    return (
      <article className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-xs text-slate-500">
          {bars === 1
            ? t('gtp56solAnalysis.horizons.sessionOne')
            : t('gtp56solAnalysis.horizons.sessionMany', { count: bars })}
        </p>
        <p role="status" className="mt-4 font-medium text-slate-300">
          {state === 'insufficient_history'
            ? t('gtp56solAnalysis.states.insufficient_history')
            : state === 'stale'
              ? t('gtp56solAnalysis.states.stale')
              : state === 'probabilitiesUnavailable'
                ? t('gtp56solAnalysis.states.probabilitiesUnavailable')
                : t('gtp56solAnalysis.states.unavailable')}
        </p>
        <p className="mt-2 text-sm text-slate-500">
          {t('gtp56solAnalysis.states.noProbabilities')}
        </p>
        {error && (
          <p role="status" className="mt-3 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            {t('gtp56solAnalysis.states.refreshWarning')}
          </p>
        )}
      </article>
    )
  }

  const direction = directionKey(result.direction)
  const confidence = confidenceKey(result.confidence)
  const sourceScope = sourceScopeKey(result.source_scope)
  const scope = sourceScope === 'asset'
    ? t('gtp56solAnalysis.sourceScope.asset')
    : sourceScope === 'asset_class'
      ? t('gtp56solAnalysis.sourceScope.asset_class')
      : t('gtp56solAnalysis.sourceScope.unknown')
  const accuracy = percent(result.validation.directional_accuracy)
  const baseline = percent(result.validation.majority_baseline_accuracy)

  return (
    <article className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <header>
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-xs text-slate-500">
          {bars === 1
            ? t('gtp56solAnalysis.horizons.sessionOne')
            : t('gtp56solAnalysis.horizons.sessionMany', { count: bars })}
        </p>
        <p className="mt-3 text-xl font-bold">
          {direction === 'bullish'
            ? t('gtp56solAnalysis.direction.bullish')
            : direction === 'bearish'
              ? t('gtp56solAnalysis.direction.bearish')
              : t('gtp56solAnalysis.direction.neutral')}
        </p>
        <p className="mt-1 text-sm text-slate-300">
          {t('gtp56solAnalysis.confidence.label', {
            level: confidence === 'high'
              ? t('gtp56solAnalysis.confidence.high')
              : confidence === 'medium'
                ? t('gtp56solAnalysis.confidence.medium')
                : t('gtp56solAnalysis.confidence.low'),
          })}
        </p>
      </header>

      {error && (
        <p role="status" className="mt-3 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {t('gtp56solAnalysis.states.refreshWarning')}
        </p>
      )}

      <div
        role="group"
        className="mt-4 space-y-2"
        aria-label={t('gtp56solAnalysis.probabilities.label')}
      >
        {OUTCOMES.map((outcome) => (
          <div key={outcome}>
            <div className="text-sm">
              <span>
                {outcomeLabel(outcome, t)}{' '}
                <span className="font-mono">{displayed[outcome]}%</span>
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={`${outcomeLabel(outcome, t)} ${displayed[outcome]}%`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={displayed[outcome]}
              className="mt-1 h-2 overflow-hidden rounded-full bg-slate-800"
            >
              <div
                className={`h-full rounded-full ${
                  outcome === 'up'
                    ? 'bg-emerald-500'
                    : outcome === 'down'
                      ? 'bg-red-500'
                      : 'bg-slate-400'
                }`}
                style={{ width: `${displayed[outcome]}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-300">
        {(Array.isArray(result.drivers) ? result.drivers : []).map((driver, index) => (
          <li key={index}>{driverText(driver, displayed, t)}</li>
        ))}
      </ul>

      <p className="mt-4 text-xs leading-5 text-slate-500">
        {t('gtp56solAnalysis.evidence.compact', {
          raw: result.sample_count,
          effective: result.effective_sample_count,
          similarity: percent(result.average_similarity),
          accuracy,
          baseline,
          scope,
          dataStart: result.period_start ? fmtDateTime(result.period_start) : '—',
          dataEnd: result.period_end ? fmtDateTime(result.period_end) : '—',
          generated: fmtDateTime(result.generated_at),
        })}
      </p>
      {result.validation.evaluated_samples === 0 && (
        <p className="mt-2 text-xs text-amber-300">
          {t('gtp56solAnalysis.evidence.noValidation')}
        </p>
      )}

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls={detailsId}
        className="mt-3 rounded text-xs font-medium text-amber-400 hover:text-amber-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
      >
        {expanded
          ? t('gtp56solAnalysis.evidence.hide')
          : t('gtp56solAnalysis.evidence.show')}
      </button>
      <div
        id={detailsId}
        hidden={!expanded}
        className="mt-3 space-y-2 border-t border-slate-800 pt-3 text-xs text-slate-400"
      >
          <p>{t('gtp56solAnalysis.evidence.methodology')}</p>
          <p>
            {t('gtp56solAnalysis.evidence.pool', {
              pool: result.candidate_pool_size,
              validation: result.validation.evaluated_samples,
              effectiveValidation: result.validation.effective_evaluated_samples,
            })}
          </p>
          {sourceScope === 'asset_class' && (
            <p className="text-amber-300">{t('gtp56solAnalysis.evidence.fallback')}</p>
          )}
          <p>
            {t('gtp56solAnalysis.evidence.periods', {
              dataStart: result.period_start ? fmtDateTime(result.period_start) : '—',
              dataEnd: result.period_end ? fmtDateTime(result.period_end) : '—',
              evidenceStart: result.evidence_period_start ? fmtDateTime(result.evidence_period_start) : '—',
              evidenceEnd: result.evidence_period_end ? fmtDateTime(result.evidence_period_end) : '—',
              generated: fmtDateTime(result.generated_at),
            })}
          </p>
      </div>
    </article>
  )
}
