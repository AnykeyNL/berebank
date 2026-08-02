import { useTranslation } from 'react-i18next'
import type { SupplementaryContext } from '../lib/types'

interface Props {
  context: SupplementaryContext | null | undefined
  namespace: 'kimiAnalysis' | 'fable5Analysis' | 'gtp56solAnalysis'
}

function isCryptoContext(
  context: SupplementaryContext,
): context is SupplementaryContext & { context_type: 'crypto' } {
  return context.context_type === 'crypto'
}

export default function SupplementaryContextPanel({ context, namespace }: Props) {
  const { t } = useTranslation()
  if (!context) return null

  const rows: { label: string; value: string }[] = []

  if (isCryptoContext(context)) {
    if (context.fear_greed_index != null) {
      rows.push({
        label: t(`${namespace}.context.fearGreed`),
        value: context.fear_greed_classification
          ? t(`${namespace}.context.fearGreedValue`, {
              index: context.fear_greed_index,
              classification: context.fear_greed_classification,
            })
          : String(context.fear_greed_index),
      })
    }
    if (context.btc_dominance != null) {
      rows.push({
        label: t(`${namespace}.context.btcDominance`),
        value: `${context.btc_dominance}%`,
      })
    }
    if (context.btc_correlation != null) {
      rows.push({
        label: t(`${namespace}.context.btcCorrelation`),
        value: context.btc_correlation,
      })
    }
    if (context.stablecoin_supply_change_pct != null) {
      rows.push({
        label: t(`${namespace}.context.stablecoinSupply`),
        value: t(`${namespace}.context.stablecoinChange`, {
          change: context.stablecoin_supply_change_pct,
        }),
      })
    }
    if (context.funding_rate_avg != null) {
      rows.push({
        label: t(`${namespace}.context.fundingRate`),
        value: t(`${namespace}.context.fundingRateValue`, {
          rate: context.funding_rate_avg,
        }),
      })
    }
    if (context.open_interest_change_percent_24h != null) {
      rows.push({
        label: t(`${namespace}.context.openInterestChange`),
        value: t(`${namespace}.context.openInterestChangeValue`, {
          change: context.open_interest_change_percent_24h,
        }),
      })
    }
    if (context.macro_regime) {
      rows.push({
        label: t(`${namespace}.context.macroRegime`),
        value: t(`${namespace}.context.macroRegimes.${context.macro_regime}`),
      })
    }
  } else {
    if (context.vix_level != null) {
      rows.push({
        label: t(`${namespace}.context.vix`),
        value: context.vix_level,
      })
    }
    if (context.yield_spread != null) {
      rows.push({
        label: t(`${namespace}.context.yieldSpread`),
        value: `${context.yield_spread}%`,
      })
    }
    if (context.macro_regime) {
      rows.push({
        label: t(`${namespace}.context.macroRegime`),
        value: t(`${namespace}.context.macroRegimes.${context.macro_regime}`),
      })
    }
    if (context.days_to_earnings != null) {
      rows.push({
        label: t(`${namespace}.context.earnings`),
        value: t(`${namespace}.context.daysToEarnings`, { days: context.days_to_earnings }),
      })
    } else if (context.earnings_near) {
      rows.push({
        label: t(`${namespace}.context.earnings`),
        value: t(`${namespace}.context.earningsNear`),
      })
    }
    if (context.insider_signal && context.insider_signal !== 'none') {
      rows.push({
        label: t(`${namespace}.context.insider`),
        value: t(`${namespace}.context.insiderSignals.${context.insider_signal}`, {
          buys: context.insider_buys ?? 0,
          sells: context.insider_sells ?? 0,
        }),
      })
    }
    if (context.sector_etf && context.sector_relative_return != null) {
      rows.push({
        label: t(`${namespace}.context.sector`, { etf: context.sector_etf }),
        value: `${context.sector_relative_return}%`,
      })
    }
  }

  if (rows.length === 0) return null

  const titleKey = isCryptoContext(context) ? `${namespace}.context.cryptoTitle` : `${namespace}.context.title`
  const subtitleKey = isCryptoContext(context)
    ? `${namespace}.context.cryptoSubtitle`
    : `${namespace}.context.subtitle`

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="font-semibold">{t(titleKey)}</h3>
      <p className="mt-1 text-xs text-slate-500">{t(subtitleKey)}</p>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg bg-slate-800/40 px-3 py-2">
            <dt className="text-[10px] uppercase tracking-wide text-slate-500">{row.label}</dt>
            <dd className="mt-0.5 text-sm font-medium text-slate-200">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
