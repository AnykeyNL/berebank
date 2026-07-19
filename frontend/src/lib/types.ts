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
  asset_class: AssetClass
  market_open: boolean | null
  last: string | null
  bid: string | null
  ask: string | null
  open: string | null
  change_24h_pct: string | null
  volume_quote: string | null
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
  order_type: 'market' | 'limit'
  status: 'open' | 'filled' | 'cancelled'
  amount: string | null
  amount_quote: string | null
  limit_price: string | null
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
  amount: string
  market: string | null
  current_price: string | null
  eur_value: string | null
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
