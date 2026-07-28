export interface Stock {
  symbol: string
  name: string
  exchange: string
  industry: string
  prev_close: string
  price: string
  open_price: string
  high_price: string
  low_price: string
  volume: number
  change: string
  change_percent: string
  updated_at: string
}

export type MarketDataStatus =
  | 'refreshing'
  | 'fresh'
  | 'partial'
  | 'delayed'
  | 'stale'
  | 'closed'
  | 'source_error'
  | 'unavailable'

export interface MarketStatus {
  provider: string
  provider_label: string
  source_type: string
  trading_mode: string
  status: MarketDataStatus
  status_label: string
  status_message: string
  session: string
  session_label: string
  session_open: boolean
  refresh_interval_seconds: number
  fresh_threshold_seconds: number
  stale_threshold_seconds: number
  last_attempt_at: string | null
  last_success_at: string | null
  latest_quote_at: string | null
  quote_age_seconds: number | null
  updated_count: number
  available_count: number
  fresh_count: number
  total_count: number
  coverage_percent: string
  fresh_coverage_percent: string
  round_coverage_percent: string
  healthy_coverage_threshold_percent: string
  batch_total_count: number
  batch_success_count: number
  batch_failed_count: number
  batch_retry_count: number
  batch_recovered_count: number
  batch_request_attempts: number
  batch_pause_count: number
  batch_fallback_used: boolean
  batch_success_percent: string
  batch_elapsed_seconds: number
  last_error: string | null
  observed_at: string
}

export interface MarketIndex {
  symbol: string
  name: string
  exchange: string
  price: string
  change: string
  change_percent: string
  turnover: string
  updated_at: string
}

export interface Account {
  username: string
  initial_cash: string
  available_cash: string
  market_value: string
  total_assets: string
  total_profit_loss: string
  total_return_percent: string
}

export interface Position {
  symbol: string
  stock_name: string
  quantity: number
  average_cost: string
  current_price: string
  market_value: string
  profit_loss: string
  profit_loss_percent: string
}

export interface Order {
  order_no: string
  symbol: string
  stock_name: string
  side: 'BUY' | 'SELL'
  order_type: string
  quantity: number
  filled_quantity: number
  price: string
  fee: string
  status: string
  reject_reason: string | null
  created_at: string
}

export interface Trade {
  trade_no: string
  order_no: string
  symbol: string
  stock_name: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: string
  amount: string
  fee: string
  created_at: string
}
