import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { usePrices } from '../lib/usePrices'
import { fmtDateTime, fmtPct, fmtPrice } from '../lib/format'
import type {
  AnalysisRange,
  AnalysisStrategy,
  AssetClass,
  Market,
  OpusAction,
  OpusAnalysis,
  OpusHorizon,
  OpusLiveTrackRecord,
  OpusMacro,
  OpusRankingRow,
  OpusRankings,
  Outlook,
  OutlookConfidence,
} from '../lib/types'
import { SignalBadge } from '../components/AnalysisCard'
import AnalysisCrossLinks from '../components/AnalysisCrossLinks'
import AssetClassIcon from '../components/AssetClassIcon'

const RANGES: AnalysisRange[] = ['1d', '1w', '30d', '90d', '180d', '365d']
const HORIZONS: OpusHorizon[] = ['1d', '1w', '4w']

const DIRECTION_STYLES: Record<Outlook['direction'], string> = {
  bullish: 'bg-emerald-500/15 text-emerald-400',
  bearish: 'bg-red-500/15 text-red-400',
  neutral: 'bg-slate-500/15 text-slate-300',
  none: 'bg-slate-800 text-slate-500',
}

const ACTION_STYLES: Record<OpusAction, string> = {
  strong_buy: 'bg-emerald-500/20 text-emerald-300',
  buy: 'bg-emerald-500/10 text-emerald-400',
  hold: 'bg-slate-700/60 text-slate-300',
  reduce: 'bg-amber-500/15 text-amber-400',
  sell: 'bg-red-500/15 text-red-400',
}

const CONFIDENCE_ORDER: OutlookConfidence[] = ['low', 'medium', 'high']

// Feature order on the detail table; mirrors FEATURE_KEYS in opus_analysis.py.
const FEATURE_ORDER = [
  'mom_21',
  'mom_63',
  'accel',
  'rev_5',
  'rev_1',
  'ma_dist',
  'adx_dir',
  'rsi_dev',
  'bb_pos',
  'range_pos',
  'vol_ratio',
  'vol_level',
  'dd_63',
  'vol_z',
  'turnover',
  'beta_mkt',
  'corr_mkt',
  'resid_mom',
  'beta_vix',
  'beta_rate',
  'beta_fng',
  'beta_stable',
  'funding',
]

type GaugeZone = 'strong_down' | 'down' | 'neutral' | 'up' | 'strong_up'

const GAUGE_ZONES: { id: GaugeZone; from: number; to: number; color: string }[] = [
  { id: 'strong_down', from: -100, to: -60, color: '#ef4444' },
  { id: 'down', from: -60, to: -20, color: '#fb923c' },
  { id: 'neutral', from: -20, to: 20, color: '#64748b' },
  { id: 'up', from: 20, to: 60, color: '#a3e635' },
  { id: 'strong_up', from: 60, to: 100, color: '#34d399' },
]

function zoneFor(score: number): GaugeZone {
  if (score >= 60) return 'strong_up'
  if (score >= 20) return 'up'
  if (score > -20) return 'neutral'
  if (score > -60) return 'down'
  return 'strong_down'
}

// Map a -100..+100 score onto the gauge semicircle (center 100,100).
function gaugePoint(radius: number, score: number): [number, number] {
  const deg = 180 - ((score + 100) / 200) * 180
  const rad = (deg * Math.PI) / 180
  return [100 + radius * Math.cos(rad), 100 - radius * Math.sin(rad)]
}

function ConvictionGauge({ score }: { score: number }) {
  const { t } = useTranslation()
  const clamped = Math.max(-100, Math.min(100, score))
  const zone = zoneFor(clamped)
  const zoneColor = GAUGE_ZONES.find((z) => z.id === zone)?.color
  const [nx, ny] = gaugePoint(58, clamped)
  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox="0 0 200 108"
        className="w-full max-w-[260px]"
        role="img"
        aria-label={`${t('opusAnalysis.gauge.label')}: ${t(`opusAnalysis.gauge.zones.${zone}`)}`}
      >
        {GAUGE_ZONES.map((z) => {
          const [x1, y1] = gaugePoint(80, z.from + 1.5)
          const [x2, y2] = gaugePoint(80, z.to - 1.5)
          return (
            <path
              key={z.id}
              d={`M ${x1} ${y1} A 80 80 0 0 1 ${x2} ${y2}`}
              fill="none"
              stroke={z.color}
              strokeWidth={13}
              strokeLinecap="round"
              opacity={zone === z.id ? 1 : 0.3}
            />
          )
        })}
        <line x1={100} y1={100} x2={nx} y2={ny} stroke="#e2e8f0" strokeWidth={3} strokeLinecap="round" />
        <circle cx={100} cy={100} r={5.5} fill="#e2e8f0" />
      </svg>
      <div className="-mt-3 text-center">
        <span className="font-mono text-2xl text-slate-100">{clamped > 0 ? `+${clamped}` : clamped}</span>
        <p className="text-sm font-semibold" style={{ color: zoneColor }}>
          {t(`opusAnalysis.gauge.zones.${zone}`)}
        </p>
      </div>
      <div className="mt-1 flex w-full max-w-[260px] justify-between text-[10px] text-slate-500">
        <span>{t('opusAnalysis.outlook.scoreMin')}</span>
        <span>{t('opusAnalysis.outlook.scoreMax')}</span>
      </div>
    </div>
  )
}

function ConfidenceDots({ confidence }: { confidence: OutlookConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <span
      className="flex items-center gap-0.5"
      title={`${t('opusAnalysis.outlook.confidenceLabel')}: ${t(`opusAnalysis.outlook.confidenceLevels.${confidence}`)}`}
    >
      {[0, 1, 2].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`} />
      ))}
    </span>
  )
}

function ConfidenceMeter({ confidence }: { confidence: OutlookConfidence }) {
  const { t } = useTranslation()
  const active = CONFIDENCE_ORDER.indexOf(confidence)
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">
        {t('opusAnalysis.outlook.confidenceLabel')}
      </p>
      <div className="mt-1 flex items-center gap-1">
        {CONFIDENCE_ORDER.map((level, i) => (
          <span
            key={level}
            className={`h-2 w-8 rounded-full ${i <= active ? 'bg-amber-400' : 'bg-slate-700'}`}
          />
        ))}
        <span className="ml-2 text-sm font-medium text-slate-200">
          {t(`opusAnalysis.outlook.confidenceLevels.${confidence}`)}
        </span>
      </div>
    </div>
  )
}

function ActionBadge({ action }: { action: OpusAction }) {
  const { t } = useTranslation()
  return (
    <span
      className={`truncate rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${ACTION_STYLES[action]}`}
    >
      {t(`opusAnalysis.actions.${action}`)}
    </span>
  )
}

/** Signed score bar: buys grow right from the centre, sells grow left. */
function ScoreBar({ score }: { score: number }) {
  const clamped = Math.max(-100, Math.min(100, score))
  const width = Math.abs(clamped) / 2
  return (
    <span className="relative block h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <span className="absolute inset-y-0 left-1/2 w-px bg-slate-700" />
      <span
        className={`absolute inset-y-0 rounded-full ${clamped >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
        style={clamped >= 0 ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
      />
    </span>
  )
}

function pctClass(value: string | null): string {
  if (value === null) return 'text-slate-500'
  const n = parseFloat(value)
  if (!Number.isFinite(n) || n === 0) return 'text-slate-400'
  return n > 0 ? 'text-emerald-400' : 'text-red-400'
}

function fmtSignedPct(value: string | null): string {
  if (value === null) return '—'
  const n = parseFloat(value)
  if (!Number.isFinite(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function MacroStrip({ macro }: { macro: OpusMacro }) {
  const { t } = useTranslation()
  const items: [string, string][] = [
    ['vix', macro.vix === null ? '—' : macro.vix.toFixed(2)],
    ['yieldCurve', macro.yield_curve === null ? '—' : `${macro.yield_curve.toFixed(2)}%`],
    ['fearGreed', macro.fear_greed === null ? '—' : String(macro.fear_greed)],
    [
      'stablecoins',
      macro.stablecoin_change_30d_pct === null ? '—' : fmtPct(macro.stablecoin_change_30d_pct),
    ],
  ]
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
      <span className="uppercase tracking-wide text-slate-500">{t('opusAnalysis.macro.title')}</span>
      {items.map(([key, value]) => (
        <span key={key}>
          {t(`opusAnalysis.macro.${key}`)} <span className="font-mono text-slate-200">{value}</span>
        </span>
      ))}
    </div>
  )
}

/** Localized note for why a row cannot simply be bought at market right now. */
function rowHints(row: OpusRankingRow, t: (key: string) => string): string[] {
  const hints: string[] = []
  if (row.stale) hints.push(t('opusAnalysis.table.staleHint'))
  if (!row.liquidity_ok) hints.push(t('opusAnalysis.table.illiquidHint'))
  if (row.low_volatility) hints.push(t('opusAnalysis.table.flatHint'))
  if (!row.tradable_now) hints.push(t('opusAnalysis.table.closedHint'))
  else if (row.requires_limit_order) hints.push(t('opusAnalysis.table.limitHint'))
  if (row.held) hints.push(t('opusAnalysis.table.heldHint'))
  return hints
}

type SortKey = 'rank' | 'asset' | 'score' | 'expected' | 'edge' | 'conviction' | 'last'
type SortDir = 'asc' | 'desc'

function RankingBoard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { prices } = usePrices()
  const [horizon, setHorizon] = useState<OpusHorizon>('1w')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [data, setData] = useState<OpusRankings | null>(null)
  const [error, setError] = useState(false)
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<'all' | AssetClass>('all')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'rank', dir: 'asc' })

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(false)
    api<OpusRankings>(`/markets/opus-rankings?horizon=${horizon}&side=${side}&limit=600`)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [horizon, side])

  const basket = useMemo(() => new Set(data?.basket ?? []), [data])

  const rows = useMemo(() => {
    const query = search.trim().toLowerCase()
    return (data?.rankings ?? []).filter((row) => {
      if (classFilter !== 'all' && row.asset_class !== classFilter) return false
      if (!query) return true
      return `${row.market} ${row.name ?? ''}`.toLowerCase().includes(query)
    })
  }, [data, search, classFilter])

  const sortedRows = useMemo(() => {
    const value = (row: OpusRankingRow): number | string | null => {
      switch (sort.key) {
        case 'rank':
          return side === 'buy' ? row.buy_rank : row.sell_rank
        case 'asset':
          return row.market
        case 'score':
          return side === 'buy' ? row.buy_score : row.sell_score
        case 'expected':
          return row.expected_return_pct === null ? null : parseFloat(row.expected_return_pct)
        case 'edge': {
          const edge = side === 'buy' ? row.net_edge_pct : row.sell_edge_pct
          return edge === null ? null : parseFloat(edge)
        }
        case 'conviction':
          return row.conviction === null ? null : Math.abs(parseFloat(row.conviction))
        case 'last': {
          const last = prices[row.market]?.last ?? row.close
          return last === null ? null : parseFloat(last)
        }
      }
    }
    const mult = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      // Rows without a value always sort last, whichever direction is active.
      if (va === null && vb === null) return 0
      if (va === null) return 1
      if (vb === null) return -1
      const cmp = typeof va === 'string' ? va.localeCompare(vb as string) : va - (vb as number)
      return cmp * mult
    })
  }, [rows, sort, side, prices])

  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'rank' || key === 'asset' ? 'asc' : 'desc' },
    )
  }

  function sortHeader(key: SortKey, label: ReactNode, className: string, title?: string) {
    const active = sort.key === key
    return (
      <button
        type="button"
        onClick={() => toggleSort(key)}
        aria-pressed={active}
        title={title}
        className={`flex items-center gap-0.5 uppercase tracking-wide transition-colors hover:text-slate-300 ${
          active ? 'text-slate-300' : ''
        } ${className}`}
      >
        <span className="truncate">{label}</span>
        {active && <span aria-hidden="true">{sort.dir === 'asc' ? '↑' : '↓'}</span>}
      </button>
    )
  }

  const btnBase =
    'rounded px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500 md:px-2 md:py-1'

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <h2 className="text-lg font-bold">{t('opusAnalysis.pageTitle')}</h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-400">{t('opusAnalysis.subtitle')}</p>
        <div className="mt-3 flex flex-wrap gap-3">
          <div
            className="flex gap-0.5 rounded-md bg-slate-800/60 p-0.5"
            role="group"
            aria-label={t('opusAnalysis.horizon')}
          >
            {HORIZONS.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h)}
                aria-pressed={horizon === h}
                className={`${btnBase} ${horizon === h ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
              >
                {t(`opusAnalysis.horizons.${h}`)}
              </button>
            ))}
          </div>
          <div className="flex gap-0.5 rounded-md bg-slate-800/60 p-0.5" role="group">
            {(['buy', 'sell'] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSide(s)}
                aria-pressed={side === s}
                className={`${btnBase} ${
                  side === s
                    ? s === 'buy'
                      ? 'bg-emerald-500/20 text-emerald-300'
                      : 'bg-red-500/20 text-red-300'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t(`opusAnalysis.sides.${s}`)}
              </button>
            ))}
          </div>
        </div>
        {data && (
          <div className="mt-3 space-y-2">
            <MacroStrip macro={data.macro} />
            <p className="text-xs text-slate-500">
              {t('opusAnalysis.rankingUpdated', {
                markets: data.markets,
                time: fmtDateTime(data.generated_at),
              })}
              {' · '}
              {Object.entries(data.regimes)
                .map(
                  ([group, regime]) =>
                    `${t(`opusAnalysis.peerGroups.${group}`)}: ${t(`opusAnalysis.regimes.${regime}`)}`,
                )
                .join(' · ')}
            </p>
            {!data.calibrated && (
              <p className="text-xs text-amber-400">{t('opusAnalysis.notCalibrated')}</p>
            )}
          </div>
        )}
      </div>

      {data && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="font-semibold">{t('opusAnalysis.basketTitle')}</h3>
          <p className="mt-1 text-xs text-slate-500">{t('opusAnalysis.basketNote')}</p>
          {data.basket.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">{t('opusAnalysis.basketEmpty')}</p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {data.basket.map((market) => {
                const row = data.rankings.find((r) => r.market === market)
                return (
                  <Link
                    key={market}
                    to={`/opus-analysis/${market}`}
                    className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-2.5 py-1.5 text-xs transition-colors hover:bg-emerald-500/10"
                  >
                    <span className="font-medium text-slate-100">{market.replace('-EUR', '')}</span>
                    {row && (
                      <span className={`font-mono ${pctClass(row.net_edge_pct)}`}>
                        {fmtSignedPct(row.net_edge_pct)}
                      </span>
                    )}
                  </Link>
                )
              })}
            </div>
          )}
        </div>
      )}

      <div className="rounded-xl border border-slate-800 bg-slate-900/60">
        <div className="border-b border-slate-800 p-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('trade.searchPlaceholder')}
            aria-label={t('opusAnalysis.pickAsset')}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm outline-none focus:border-amber-500"
          />
          <div className="mt-2 flex gap-1">
            {(['all', 'crypto', 'stock', 'fund', 'commodity'] as const).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setClassFilter(c)}
                title={t(`trade.filter.${c}`)}
                aria-label={t(`trade.filter.${c}`)}
                aria-pressed={classFilter === c}
                className={`flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors ${
                  classFilter === c
                    ? 'bg-amber-500/15 text-amber-400'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {c === 'all' ? (
                  <span className="text-xs font-medium">{t('trade.filter.all')}</span>
                ) : (
                  <AssetClassIcon assetClass={c} className="h-5 w-5" />
                )}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="p-6 text-center text-sm text-slate-500">{t('opusAnalysis.loadError')}</p>
        )}
        {!error && !data && <div className="m-3 h-64 animate-pulse rounded-lg bg-slate-800/40" />}

        {data && (
          <div className="max-h-[65vh] overflow-y-auto">
            <div className="flex items-center gap-2 border-b border-slate-800/60 px-4 py-1.5 text-[10px] text-slate-500">
              {sortHeader('rank', t('opusAnalysis.table.rank'), 'w-7 justify-end')}
              {sortHeader('asset', t('opusAnalysis.table.asset'), 'flex-1')}
              {sortHeader(
                'score',
                t('opusAnalysis.table.score'),
                'hidden w-24 justify-end sm:flex',
                t('opusAnalysis.table.scoreTitle'),
              )}
              {sortHeader(
                'expected',
                t('opusAnalysis.table.expected'),
                'hidden w-14 justify-end md:flex',
                t('opusAnalysis.table.expectedTitle'),
              )}
              {sortHeader(
                'edge',
                t('opusAnalysis.table.edge'),
                'w-14 justify-end',
                t('opusAnalysis.table.edgeTitle'),
              )}
              {sortHeader(
                'conviction',
                t('opusAnalysis.table.conviction'),
                'hidden w-12 justify-center lg:flex',
                t('opusAnalysis.table.convictionTitle'),
              )}
              <span className="w-16 text-center uppercase tracking-wide sm:w-20">
                {t('opusAnalysis.table.action')}
              </span>
              {sortHeader('last', t('trade.last'), 'w-16 justify-end sm:w-20')}
            </div>

            {sortedRows.length === 0 && (
              <p className="p-6 text-center text-sm text-slate-500">{t('opusAnalysis.empty')}</p>
            )}

            {sortedRows.map((row) => {
              const last = prices[row.market]?.last ?? row.close
              const edge = side === 'buy' ? row.net_edge_pct : row.sell_edge_pct
              const score = side === 'buy' ? row.buy_score : -row.sell_score
              const hints = rowHints(row, t)
              return (
                <button
                  key={row.market}
                  type="button"
                  onClick={() => navigate(`/opus-analysis/${row.market}`)}
                  title={hints.join(' · ') || undefined}
                  className={`flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition-colors hover:bg-slate-800/50 ${
                    row.tradable ? '' : 'opacity-60'
                  }`}
                >
                  <span className="w-7 text-right font-mono text-xs text-slate-500">
                    {side === 'buy' ? row.buy_rank : row.sell_rank}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 font-medium">
                      <AssetClassIcon assetClass={row.asset_class} className="h-3.5 w-3.5 shrink-0" />
                      {row.market.replace('-EUR', '')}
                      {basket.has(row.market) && (
                        <span
                          className="rounded bg-emerald-500/15 px-1 py-0.5 text-[9px] font-semibold uppercase text-emerald-400"
                          title={t('opusAnalysis.basketNote')}
                        >
                          {t('opusAnalysis.inBasket')}
                        </span>
                      )}
                      {row.suggested_order_type === 'limit' && (
                        <span
                          className="text-[9px] uppercase tracking-wide text-amber-500"
                          title={hints.join(' · ')}
                        >
                          {t('common.limit')}
                        </span>
                      )}
                    </span>
                    {row.name && <span className="block truncate text-xs text-slate-500">{row.name}</span>}
                  </span>
                  <span className="hidden w-24 items-center gap-1.5 sm:flex">
                    <ScoreBar score={score} />
                    <span className="w-6 shrink-0 text-right font-mono text-[10px] text-slate-400">
                      {Math.abs(score)}
                    </span>
                  </span>
                  <span
                    className={`hidden w-14 text-right font-mono text-xs md:block ${pctClass(row.expected_return_pct)}`}
                  >
                    {fmtSignedPct(row.expected_return_pct)}
                  </span>
                  <span className={`w-14 text-right font-mono text-xs ${pctClass(edge)}`}>
                    {fmtSignedPct(edge)}
                  </span>
                  <span className="hidden w-12 justify-center lg:flex">
                    <ConfidenceDots confidence={row.confidence} />
                  </span>
                  <span className="flex w-16 justify-center sm:w-20">
                    <ActionBadge action={row.action} />
                  </span>
                  <span className="w-16 text-right font-mono text-xs sm:w-20">{fmtPrice(last)}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      <p className="text-xs text-slate-500">{t('opusAnalysis.disclaimer')}</p>
    </div>
  )
}

function Chip({
  label,
  value,
  tone = 'neutral',
  title,
}: {
  label: string
  value: string
  tone?: 'neutral' | 'good' | 'bad'
  title?: string
}) {
  const toneClass =
    tone === 'good' ? 'text-emerald-300' : tone === 'bad' ? 'text-red-300' : 'text-slate-200'
  return (
    <div className="rounded-lg bg-slate-800/40 px-3 py-2" title={title}>
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`font-mono text-sm ${toneClass}`}>{value}</p>
    </div>
  )
}

function LiveTrackRecordCard({
  record,
  label,
}: {
  record: OpusLiveTrackRecord | null
  label: string
}) {
  const { t } = useTranslation()
  if (!record) return null
  return (
    <div className="rounded-lg bg-slate-800/30 p-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm text-slate-300">
        {t('opusAnalysis.trackRecord.liveSummary', {
          hitRate: record.hit_rate_pct,
          samples: record.samples,
          horizon: t(`opusAnalysis.horizons.${record.horizon}`),
        })}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        {t('opusAnalysis.trackRecord.livePeriod', { from: record.from, to: record.to })}
      </p>
      <div className="mt-2 flex flex-wrap gap-2 text-xs">
        {record.avg_buy_return_pct !== null && (
          <span className="rounded bg-emerald-500/10 px-2 py-1 font-mono text-emerald-300">
            {t('opusAnalysis.trackRecord.liveBuys', {
              samples: record.buy_samples,
              value: record.avg_buy_return_pct,
            })}
          </span>
        )}
        {record.avg_sell_return_pct !== null && (
          <span className="rounded bg-red-500/10 px-2 py-1 font-mono text-red-300">
            {t('opusAnalysis.trackRecord.liveSells', {
              samples: record.sell_samples,
              value: record.avg_sell_return_pct,
            })}
          </span>
        )}
      </div>
    </div>
  )
}

export default function OpusAnalysisPage() {
  const { t } = useTranslation()
  const { market: marketParam } = useParams()
  const { prices } = usePrices()

  const market = (marketParam ?? '').toUpperCase()
  const [range, setRange] = useState<AnalysisRange>('30d')
  const [horizon, setHorizon] = useState<OpusHorizon>('1w')
  const [analysis, setAnalysis] = useState<OpusAnalysis | null>(null)
  const [error, setError] = useState(false)
  const [markets, setMarkets] = useState<Market[]>([])
  const [showFeatures, setShowFeatures] = useState(false)

  useEffect(() => {
    if (market) api<Market[]>('/markets').then(setMarkets).catch(() => {})
  }, [market])

  useEffect(() => {
    if (!market) return
    let cancelled = false
    setAnalysis(null)
    setError(false)

    function load() {
      api<OpusAnalysis>(
        `/markets/${encodeURIComponent(market)}/opus-analysis?range=${range}&horizon=${horizon}`,
      )
        .then((data) => {
          if (!cancelled) {
            setAnalysis(data)
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
  }, [market, range, horizon])

  const marketInfo = markets.find((m) => m.market === market)
  const livePrice = prices[market]?.last ?? marketInfo?.last ?? null

  const changePct = useMemo(() => {
    if (!analysis || analysis.candles.length < 2) return null
    const first = parseFloat(analysis.candles[0][4])
    const last = parseFloat(analysis.candles[analysis.candles.length - 1][4])
    return first !== 0 ? ((last - first) / first) * 100 : null
  }, [analysis])

  if (!market) return <RankingBoard />

  const outlook = analysis?.outlook ?? null
  const rec = analysis?.recommendation ?? null
  const calibration = analysis?.calibration ?? null
  const trackRecord = analysis?.track_record ?? null

  function featureReason(strategy: AnalysisStrategy): string {
    const params: Record<string, string> = {}
    for (const [key, value] of Object.entries(strategy.reason.params)) {
      params[key] = String(value ?? '—')
    }
    return t(`opusAnalysis.reasons.${strategy.reason.code}`, params)
  }

  const btnBase =
    'rounded px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-slate-500 md:px-2 md:py-1'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3 text-xs">
              <Link to={`/trade/${market}`} className="text-amber-400 hover:text-amber-300">
                ← {t('opusAnalysis.backToTrade')}
              </Link>
              <Link to="/opus-analysis" className="text-slate-400 hover:text-slate-200">
                {t('opusAnalysis.changeAsset')}
              </Link>
              <AnalysisCrossLinks market={market} current="opus" />
            </div>
            <h2 className="mt-1 flex items-center gap-2 text-xl font-bold">
              {marketInfo && <AssetClassIcon assetClass={marketInfo.asset_class} className="h-5 w-5" />}
              {t('opusAnalysis.pageTitle')}: {market}
            </h2>
            {marketInfo?.name && <p className="text-sm text-slate-300">{marketInfo.name}</p>}
          </div>
          <div className="text-right">
            <p className="text-xs uppercase tracking-wide text-slate-500">{t('trade.last')}</p>
            <p className="font-mono text-lg">{fmtPrice(livePrice)}</p>
            {changePct !== null && (
              <p className={`text-sm font-medium ${changePct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {fmtPct(changePct)} · {t(`chart.ranges.${range}`)}
              </p>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          <div
            className="flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5"
            role="group"
            aria-label={t('opusAnalysis.horizon')}
          >
            {HORIZONS.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h)}
                aria-pressed={horizon === h}
                className={`${btnBase} ${horizon === h ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
              >
                {t(`opusAnalysis.horizons.${h}`)}
              </button>
            ))}
          </div>
          <div
            className="flex flex-wrap gap-0.5 rounded-md bg-slate-800/60 p-0.5"
            role="group"
            aria-label={t('opusAnalysis.range')}
          >
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                aria-pressed={range === r}
                className={`${btnBase} ${range === r ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
              >
                {t(`chart.ranges.${r}`)}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-500">
          {t('opusAnalysis.loadErrorDetail')}
        </div>
      )}
      {!error && !analysis && <div className="h-64 animate-pulse rounded-xl bg-slate-800/40" />}

      {analysis && outlook && rec && (
        <>
          {/* Verdict hero */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">
                  {t('opusAnalysis.outlook.title')}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-block rounded-md px-3 py-1 text-lg font-bold uppercase tracking-wide ${DIRECTION_STYLES[outlook.direction]}`}
                  >
                    {t(`analyze.signals.${outlook.direction}`)}
                  </span>
                  <span
                    className={`rounded-md px-2.5 py-1 text-sm font-bold uppercase tracking-wide ${ACTION_STYLES[rec.action]}`}
                  >
                    {t(`opusAnalysis.actions.${rec.action}`)}
                  </span>
                </div>
                <div className="mt-2 flex gap-2 text-xs font-medium">
                  <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-emerald-400">
                    {t('opusAnalysis.outlook.buyScore', { value: rec.buy_score })}
                  </span>
                  <span className="rounded bg-red-500/10 px-2 py-0.5 text-red-400">
                    {t('opusAnalysis.outlook.sellScore', { value: rec.sell_score })}
                  </span>
                </div>
                <p className="mt-2 max-w-md text-sm text-slate-300">
                  {t(`opusAnalysis.outlook.reasons.${outlook.reason.code}`, outlook.reason.params)}
                </p>
                <p className="mt-1 max-w-md text-xs text-slate-500">
                  {t(`opusAnalysis.outlook.regimeNote.${outlook.regime}`)}
                </p>
                <div className="mt-4">
                  <ConfidenceMeter confidence={outlook.confidence} />
                </div>
              </div>
              <div className="w-full max-w-xs">
                <ConvictionGauge score={outlook.score} />
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              <Chip
                label={t('opusAnalysis.recommendation.expectedReturn')}
                value={fmtSignedPct(rec.expected_return_pct)}
                tone={
                  rec.expected_return_pct === null
                    ? 'neutral'
                    : parseFloat(rec.expected_return_pct) >= 0
                      ? 'good'
                      : 'bad'
                }
                title={t('opusAnalysis.recommendation.horizonBars', { bars: rec.horizon_bars })}
              />
              <Chip
                label={t('opusAnalysis.recommendation.netEdge')}
                value={fmtSignedPct(rec.net_edge_pct)}
                tone={
                  rec.net_edge_pct === null
                    ? 'neutral'
                    : parseFloat(rec.net_edge_pct) > 0
                      ? 'good'
                      : 'bad'
                }
                title={t('opusAnalysis.table.edgeTitle')}
              />
              <Chip
                label={t('opusAnalysis.recommendation.netEdgeLimit')}
                value={fmtSignedPct(rec.net_edge_limit_pct)}
                tone={
                  rec.net_edge_limit_pct === null
                    ? 'neutral'
                    : parseFloat(rec.net_edge_limit_pct) > 0
                      ? 'good'
                      : 'bad'
                }
              />
              <Chip
                label={t('opusAnalysis.recommendation.sellEdge')}
                value={fmtSignedPct(rec.sell_edge_pct)}
              />
              <Chip
                label={t('opusAnalysis.recommendation.alpha')}
                value={fmtSignedPct(rec.alpha_pct)}
              />
              <Chip
                label={t('opusAnalysis.recommendation.marketReturn')}
                value={fmtSignedPct(rec.market_return_pct)}
              />
              <Chip
                label={t('opusAnalysis.recommendation.expectedMove')}
                value={rec.expected_move_pct === null ? '—' : `${rec.expected_move_pct}%`}
                title={t('opusAnalysis.recommendation.horizonBars', { bars: rec.horizon_bars })}
              />
              <Chip
                label={t('opusAnalysis.recommendation.fee')}
                value={rec.fee_pct === null ? '—' : `${rec.fee_pct}%`}
                title={t('opusAnalysis.recommendation.limitFee') + `: ${rec.limit_fee_pct ?? '—'}%`}
              />
              <Chip
                label={t('opusAnalysis.recommendation.conviction')}
                value={rec.conviction ?? '—'}
                title={t('opusAnalysis.table.convictionTitle')}
              />
              {rec.suggested_stop_price !== null && rec.suggested_stop_pct !== null && (
                <Chip
                  label={t('opusAnalysis.recommendation.stop')}
                  value={t('opusAnalysis.recommendation.stopValue', {
                    price: fmtPrice(rec.suggested_stop_price),
                    pct: rec.suggested_stop_pct,
                  })}
                />
              )}
              {analysis.gates && (
                <Chip
                  label={t('opusAnalysis.recommendation.orderType')}
                  value={t(
                    `opusAnalysis.recommendation.orderTypes.${analysis.gates.suggested_order_type}`,
                  )}
                  title={
                    analysis.gates.tradable_now ? undefined : t('opusAnalysis.table.closedHint')
                  }
                />
              )}
            </div>

            {analysis.gates && (
              <p className="mt-3 text-xs text-amber-400/90">
                {[
                  analysis.gates.stale ? t('opusAnalysis.table.staleHint') : null,
                  analysis.gates.liquidity_ok ? null : t('opusAnalysis.table.illiquidHint'),
                  analysis.gates.low_volatility ? t('opusAnalysis.table.flatHint') : null,
                  analysis.gates.tradable_now ? null : t('opusAnalysis.table.closedHint'),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            )}
            <p className="mt-3 text-xs text-slate-500">
              {t('opusAnalysis.updated', { time: fmtDateTime(analysis.generated_at) })}
            </p>
          </div>

          {/* Cross-section + features */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="font-semibold">{t('opusAnalysis.crossSection.title')}</h3>
            <p className="mt-1 text-sm text-slate-300">
              {t(`opusAnalysis.crossSection.mode.${analysis.mode}`)}
            </p>
            {analysis.cross_section && (
              <p className="mt-1 text-xs text-slate-500">
                {t('opusAnalysis.crossSection.peers', {
                  peers: analysis.cross_section.peers,
                  group: t(`opusAnalysis.peerGroups.${analysis.cross_section.peer_group}`),
                })}
                {' · '}
                {t('opusAnalysis.crossSection.day', {
                  day: analysis.cross_section.day.slice(0, 10),
                })}
              </p>
            )}

            <button
              type="button"
              onClick={() => setShowFeatures((v) => !v)}
              className="mt-3 text-xs font-medium text-amber-400 hover:text-amber-300"
              aria-expanded={showFeatures}
            >
              {showFeatures
                ? t('opusAnalysis.features.hide')
                : t('opusAnalysis.features.show', {
                    count: Object.keys(analysis.strategies).length,
                  })}
            </button>

            {showFeatures && (
              <>
                <p className="mt-3 text-xs text-slate-500">{t('opusAnalysis.features.note')}</p>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr className="border-b border-slate-800 text-left text-[10px] uppercase tracking-wide text-slate-500">
                        <th className="py-1.5 pr-2 font-normal">{t('opusAnalysis.features.feature')}</th>
                        <th className="py-1.5 pr-2 text-right font-normal">
                          {t('opusAnalysis.features.percentile')}
                        </th>
                        <th className="py-1.5 pr-2 text-right font-normal">
                          {t('opusAnalysis.features.weight')}
                        </th>
                        <th
                          className="py-1.5 pr-2 text-right font-normal"
                          title={t('opusAnalysis.features.icTitle')}
                        >
                          {t('opusAnalysis.features.ic')}
                        </th>
                        <th className="py-1.5 pr-2 text-right font-normal">
                          {t('opusAnalysis.features.contribution')}
                        </th>
                        <th className="py-1.5 font-normal" />
                      </tr>
                    </thead>
                    <tbody>
                      {FEATURE_ORDER.filter((key) => analysis.strategies[key]).map((key) => {
                        const strategy = analysis.strategies[key]
                        const values = strategy.values
                        return (
                          <tr key={key} className="border-b border-slate-800/40 align-top">
                            <td className="py-2 pr-2">
                              <span className="flex flex-wrap items-center gap-1.5">
                                <span
                                  className="font-medium text-slate-200"
                                  title={strategy.explanation}
                                >
                                  {t(`opusAnalysis.featureNames.${key}`)}
                                </span>
                                <SignalBadge signal={strategy.signal} />
                              </span>
                              <span className="block text-xs text-slate-500">
                                {featureReason(strategy)}
                              </span>
                            </td>
                            <td className="py-2 pr-2 text-right font-mono text-xs text-slate-300">
                              {values.percentile ?? '—'}
                            </td>
                            <td className="py-2 pr-2 text-right font-mono text-xs text-slate-400">
                              {values.weight ?? '—'}
                            </td>
                            <td className="py-2 pr-2 text-right font-mono text-xs text-slate-400">
                              {values.ic ?? '—'}
                            </td>
                            <td
                              className={`py-2 pr-2 text-right font-mono text-xs ${pctClass(values.contribution ?? null)}`}
                            >
                              {values.contribution ?? '—'}
                            </td>
                            <td className="py-2 text-right font-mono text-xs text-slate-500">
                              {values.value ?? ''}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          {/* Calibration provenance */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="font-semibold">{t('opusAnalysis.calibration.title')}</h3>
            {calibration && calibration.weights_learned ? (
              <>
                <p className="mt-2 text-sm text-slate-300">
                  {t('opusAnalysis.calibration.summary', {
                    group: t(`opusAnalysis.peerGroups.${calibration.peer_group}`),
                    horizon: t(`opusAnalysis.horizons.${calibration.horizon}`),
                    regime: t(`opusAnalysis.regimes.${calibration.regime}`),
                    days: calibration.days,
                    from: calibration.from,
                    to: calibration.to,
                  })}
                </p>
                <div className="mt-2 space-y-1 text-xs text-slate-500">
                  {calibration.walk_forward_ic !== null && (
                    <p>
                      {t('opusAnalysis.calibration.ic', {
                        ic: calibration.walk_forward_ic,
                        days: calibration.walk_forward_ic_days,
                      })}
                    </p>
                  )}
                  {calibration.walk_forward_hit_rate_pct !== null && (
                    <p>
                      {t('opusAnalysis.calibration.hitRate', {
                        value: calibration.walk_forward_hit_rate_pct,
                        samples: calibration.walk_forward_samples,
                      })}
                    </p>
                  )}
                  {calibration.market_return_pct !== null && (
                    <p>
                      {t('opusAnalysis.calibration.drift', {
                        value: calibration.market_return_pct,
                        std: calibration.market_return_std_pct,
                      })}
                    </p>
                  )}
                  {calibration.calibrated_at && (
                    <p>
                      {t('opusAnalysis.calibration.calibratedAt', {
                        time: fmtDateTime(calibration.calibrated_at),
                      })}
                      {' · '}
                      {t('opusAnalysis.calibration.engine', { version: calibration.engine_version })}
                    </p>
                  )}
                </div>
                {calibration.top_features.length > 0 && (
                  <div className="mt-3">
                    <p className="text-[10px] uppercase tracking-wide text-slate-500">
                      {t('opusAnalysis.calibration.topFeatures')}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
                      {calibration.top_features.map((item) => (
                        <span
                          key={item.feature}
                          className="rounded bg-slate-800/60 px-2 py-0.5 text-slate-300"
                        >
                          {t(`opusAnalysis.featureNames.${item.feature}`)}
                          <span className="ml-1 font-mono text-slate-500">{item.weight}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <p className="mt-2 text-sm text-slate-400">{t('opusAnalysis.calibration.prior')}</p>
            )}
          </div>

          {/* Track records */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="font-semibold">{t('opusAnalysis.trackRecord.title', { market })}</h3>
            {trackRecord ? (
              <>
                <p className="mt-2 text-sm text-slate-300">
                  {t('opusAnalysis.trackRecord.summary', {
                    hitRate: trackRecord.hit_rate_pct,
                    days: trackRecord.forward_days,
                  })}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t('opusAnalysis.trackRecord.samples', {
                    samples: trackRecord.samples,
                    from: trackRecord.from,
                    to: trackRecord.to,
                  })}
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {trackRecord.avg_bullish_return_pct !== null && (
                    <span className="rounded bg-emerald-500/10 px-2 py-1 font-mono text-emerald-300">
                      {t('opusAnalysis.trackRecord.avgBullish', {
                        value: trackRecord.avg_bullish_return_pct,
                      })}
                    </span>
                  )}
                  {trackRecord.avg_bearish_return_pct !== null && (
                    <span className="rounded bg-red-500/10 px-2 py-1 font-mono text-red-300">
                      {t('opusAnalysis.trackRecord.avgBearish', {
                        value: trackRecord.avg_bearish_return_pct,
                      })}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="mt-2 text-sm text-slate-500">{t('opusAnalysis.trackRecord.noHistory')}</p>
            )}

            <h4 className="mt-4 text-sm font-semibold">{t('opusAnalysis.trackRecord.liveTitle')}</h4>
            {analysis.live_track_record || analysis.live_track_record_all ? (
              <div className="mt-2 space-y-2">
                <LiveTrackRecordCard
                  record={analysis.live_track_record}
                  label={t('opusAnalysis.trackRecord.liveMarket', { market })}
                />
                <LiveTrackRecordCard
                  record={analysis.live_track_record_all}
                  label={t('opusAnalysis.trackRecord.liveAll')}
                />
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-500">{t('opusAnalysis.trackRecord.liveNone')}</p>
            )}
          </div>
        </>
      )}

      <p className="text-xs text-slate-500">{t('opusAnalysis.disclaimer')}</p>
    </div>
  )
}
