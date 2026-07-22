export interface User {
  id: number
  email: string
  display_name: string
  role: 'user' | 'bank_manager'
  is_active: boolean
  preferred_language: 'en' | 'nl' | null
  mcp_trading_enabled: boolean
}

export type AssetClass = 'crypto' | 'stock' | 'fund'

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
