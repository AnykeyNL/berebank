export interface User {
  id: number
  email: string
  display_name: string
  role: 'user' | 'bank_manager'
  is_active: boolean
  preferred_language: 'en' | 'nl' | null
  mcp_trading_enabled: boolean
}

export type AssetClass = 'crypto' | 'stock' | 'fund' | 'commodity'

export interface Market {
  market: string
  base: string
  quote: string
  name: string | null
  listing: string | null
  asset_class: AssetClass
  market_open: boolean | null
  last: string | null
  bid: string | null
  ask: string | null
  open: string | null
  change_24h_pct: string | null
  volume_quote: string | null
  has_news: boolean
}

export interface NewsItem {
  id: string
  datetime: string
  title: string
  body: string
  language: string[]
  url?: string | null
  source?: string | null
}

export interface NewsPage {
  items: NewsItem[]
  page: number
  page_size: number
  total_pages: number
  total_count: number
}

// [timestamp_ms, open, high, low, close, volume]
export type Candle = [number, string, string, string, string, string]

export type AnalysisSignal = 'bullish' | 'bearish' | 'neutral' | 'none'
export type AnalysisRange = '1d' | '1w' | '30d' | '90d' | '180d' | '365d'

// [timestamp_ms, value] — value is null while the indicator is undefined
export type IndicatorPoint = [number, string | null]

export interface AnalysisLevel {
  price: string | null
  strength: number
}

export interface AnalysisStrategy {
  signal: AnalysisSignal
  reason: { code: string; params: Record<string, string | number | null> }
  explanation: string
  values: Record<string, string | null>
  series: Record<string, IndicatorPoint[]>
  levels?: AnalysisLevel[]
}

export interface Analysis {
  market: string
  range: AnalysisRange
  generated_at: string
  candles: Candle[]
  strategies: {
    trend: AnalysisStrategy
    rsi: AnalysisStrategy
    macd: AnalysisStrategy
    volatility: AnalysisStrategy
    levels_volume: AnalysisStrategy
  }
}

export type OutlookConfidence = 'high' | 'medium' | 'low'
export type MarketRegime = 'trending' | 'ranging' | 'neutral'

export interface OutlookContribution {
  strategy: string
  signal: AnalysisSignal
  weight: number
}

export interface Outlook {
  direction: AnalysisSignal
  score: number // -100 (strongly bearish) to +100 (strongly bullish)
  // Fable5 and KimiK3: 0..100 shares of active signal weight voting bullish/bearish.
  buy_score?: number
  sell_score?: number
  confidence: OutlookConfidence
  regime: MarketRegime
  reason: { code: string; params: Record<string, string | number | null> }
  contributions: OutlookContribution[]
}

export interface TrackRecord {
  hit_rate_pct: string
  samples: number
  forward_days: number
  avg_bullish_return_pct: string | null
  avg_bearish_return_pct: string | null
  from: string
  to: string
}

export interface SupplementaryContext {
  context_type?: 'crypto' | null
  macro_regime: 'risk_on' | 'risk_off' | 'neutral' | null
  vix_level?: string | null
  vix_change_pct?: string | null
  us2y_yield?: string | null
  us10y_yield?: string | null
  yield_spread?: string | null
  days_to_earnings?: number | null
  earnings_near?: boolean
  insider_signal?: 'bullish' | 'bearish' | 'neutral' | 'none' | null
  insider_buys?: number
  insider_sells?: number
  sector_etf?: string | null
  sector_relative_return?: string | null
  fear_greed_index?: number | null
  fear_greed_classification?: string | null
  fear_greed_change?: string | null
  btc_dominance?: string | null
  btc_dominance_change_pct?: string | null
  btc_correlation?: string | null
  stablecoin_supply_usd?: string | null
  stablecoin_supply_change_pct?: string | null
  funding_rate_avg?: string | null
  funding_rate_change_24h?: string | null
  open_interest_change_percent_24h?: string | null
  open_interest_change_percent_4h?: string | null
  open_interest_change_percent_1h?: string | null
  open_interest_usd?: string | null
  long_short_ratio?: string | null
  long_liquidation_usd_24h?: string | null
  short_liquidation_usd_24h?: string | null
}

export interface KimiAnalysis {
  market: string
  range: AnalysisRange
  generated_at: string
  candles: Candle[]
  outlook: Outlook
  strategies: Analysis['strategies'] & {
    trend_strength: AnalysisStrategy
    momentum: AnalysisStrategy
    stochastic: AnalysisStrategy
    fear_greed_regime?: AnalysisStrategy
    crypto_liquidity?: AnalysisStrategy
    funding_regime?: AnalysisStrategy
    funding_momentum?: AnalysisStrategy
    oi_momentum?: AnalysisStrategy
    oi_fast?: AnalysisStrategy
    long_short?: AnalysisStrategy
    liquidations?: AnalysisStrategy
    vix_regime?: AnalysisStrategy
    yield_curve?: AnalysisStrategy
    relative_strength?: AnalysisStrategy
    event_risk?: AnalysisStrategy
    insider_flow?: AnalysisStrategy
  }
  track_record: TrackRecord | null
  context: SupplementaryContext | null
}

export interface OutlookSummary {
  direction: AnalysisSignal
  score: number
  // Fable5 and KimiK3: 0..100 shares of active signal weight voting bullish/bearish.
  buy_score?: number
  sell_score?: number
  confidence: OutlookConfidence
  regime: MarketRegime
}

export interface KimiOutlooks {
  generated_at: string
  outlooks: Record<string, OutlookSummary>
}

export interface TechnicalOutlooks {
  generated_at: string
  outlooks: Record<string, OutlookSummary>
}

export interface Fable5Analysis {
  market: string
  range: AnalysisRange
  generated_at: string
  candles: Candle[]
  outlook: Outlook
  strategies: Analysis['strategies'] & {
    momentum: AnalysisStrategy
    stochastic: AnalysisStrategy
    trend_strength: AnalysisStrategy
    vix_regime?: AnalysisStrategy
    yield_curve?: AnalysisStrategy
    funding_regime?: AnalysisStrategy
    oi_momentum?: AnalysisStrategy
    long_short?: AnalysisStrategy
    liquidations?: AnalysisStrategy
    relative_strength?: AnalysisStrategy
    event_risk?: AnalysisStrategy
  }
  track_record: TrackRecord | null
  context: SupplementaryContext | null
}

export interface Fable5Outlooks {
  generated_at: string
  outlooks: Record<string, OutlookSummary>
}

export type GTP56SolStatus = 'ok' | 'insufficient_history' | 'stale' | 'unavailable'
export type GTP56SolHorizon = '1d' | '1w' | '1m'
export type GTP56SolDirection = 'bullish' | 'bearish' | 'neutral'
export type GTP56SolConfidence = 'low' | 'medium' | 'high'
export type GTP56SolSourceScope = 'asset' | 'asset_class'

export interface GTP56SolProbabilities {
  up: string
  sideways: string
  down: string
}

export interface GTP56SolDriver {
  code:
    | 'historical_probability_leader'
    | 'technical_vote_balance'
    | 'walk_forward_evidence'
    | 'macro_vix_context'
    | 'macro_yield_spread'
    | 'macro_us2y_yield'
    | 'macro_fear_greed'
    | 'macro_btc_dominance'
    | 'macro_btc_correlation'
    | 'macro_stablecoin_supply'
    | 'macro_funding_rate'
    | 'macro_open_interest'
    | 'earnings_near'
    | 'insider_activity'
  params: Record<string, string | number | null>
}

export interface GTP56SolValidationEvidence {
  evaluated_samples: number
  effective_evaluated_samples: number
  directional_accuracy: string | null
  majority_baseline_accuracy: string | null
  period_start: string | null
  period_end: string | null
}

export interface GTP56SolAnalysis {
  market: string
  asset_class: AssetClass
  generated_at: string
  status: GTP56SolStatus
  horizon: GTP56SolHorizon
  source_scope: GTP56SolSourceScope
  probabilities: GTP56SolProbabilities | null
  direction: GTP56SolDirection
  confidence: GTP56SolConfidence
  drivers: GTP56SolDriver[]
  sample_count: number
  effective_sample_count: number
  candidate_pool_size: number
  average_similarity: string | null
  validation: GTP56SolValidationEvidence
  period_start: string | null
  period_end: string | null
  evidence_period_start: string | null
  evidence_period_end: string | null
  context: SupplementaryContext | null
}

export interface GTP56SolOutlookSummary {
  direction: GTP56SolDirection
  score: number
  confidence: GTP56SolConfidence
}

export interface GTP56SolOutlooks {
  generated_at: string
  horizon: GTP56SolHorizon
  outlooks: Record<string, GTP56SolOutlookSummary>
}

export interface PriceUpdate {
  market: string
  last: string | null
  bid: string | null
  ask: string | null
  open: string | null
  volume_quote: string | null
  timestamp: number
  market_open?: boolean
}

export interface Order {
  id: number
  market: string
  side: 'buy' | 'sell'
  order_type: 'market' | 'limit' | 'stop_loss'
  status: 'open' | 'filled' | 'cancelled'
  amount: string | null
  amount_quote: string | null
  limit_price: string | null
  trigger_price: string | null
  fee_paid: string | null
  filled_price: string | null
  created_at: string
  filled_at: string | null
}

export interface Trade {
  id: number
  market: string
  side: 'buy' | 'sell'
  amount: string
  price: string
  eur_value: string
  fee_eur: string
  created_at: string
}

export interface TradePnl extends Trade {
  pnl_eur: string | null
  pnl_pct: string | null
  held_seconds: number | null
}

export interface Holding {
  asset: string
  amount: string // available (not reserved) amount
  reserved: string // amount locked in open limit sell orders
  market: string | null
  name: string | null
  listing: string | null
  current_price: string | null
  eur_value: string | null // values amount + reserved at the live price
}

export interface FeeTier {
  volume_30d_eur: string
  maker_pct: string
  taker_pct: string
}

export interface PortfolioSnapshot {
  created_at: string
  total_value_eur: string
  asset_count: number
}

export interface Portfolio {
  balance_eur: string
  reserved_eur: string
  holdings: Holding[]
  holdings_value_eur: string
  total_value_eur: string
  fee_tier: FeeTier
}

export interface LeaderboardEntry {
  user_id: number
  display_name: string
  trades: number
  cash_eur: string
  assets_eur: string
  total_eur: string
}

export interface AdminUser extends User {
  balance_eur: string
  created_at: string
}

export interface Settings {
  bitvavo_api_key_masked: string | null
  has_api_secret: boolean
  connection: {
    connected: boolean
    markets: number
    prices_cached: number
    last_update: number | null
  }
  twelvedata_api_key_masked: string | null
  twelvedata: {
    configured: boolean
    connected: boolean
    markets: number
    prices_cached: number
    last_update: number | null
    usd_eur: string | null
    error: string | null
  }
  coinglass_api_key_masked: string | null
  coinglass: {
    configured: boolean
    last_update: number | null
    error: string | null
  }
}

export interface RssFeed {
  id: number
  url: string
  name: string
  enabled: boolean
  last_fetched_at: string | null
  last_error: string | null
  created_at: string
}

export interface RssFeedStatus {
  feeds: RssFeed[]
  aggregator: {
    feeds: number
    enabled_feeds: number
    articles: number
    last_poll: string | null
    last_error: string | null
  }
}

export interface CandleHistoryStatus {
  market_count: number
  candle_count: number
  first_day: string | null
  last_day: string | null
  gtp56sol_deep_markets: number
  last_harvest: string | null
}

export interface CandleHistoryImportResult {
  markets_imported: number
  rows_written: number
  settings_imported: number
  skipped_invalid: number
}
