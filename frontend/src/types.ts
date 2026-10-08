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
  quote_source_at: string | null
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
  realized_profit_loss: string
  floating_profit_loss: string
  total_return_percent: string
}

export interface Position {
  symbol: string
  stock_name: string
  quantity: number
  sellable_quantity: number
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
  limit_price: string | null
  submitted_quote_price: string | null
  submitted_quote_at: string | null
  filled_quote_at: string | null
  quantity: number
  filled_quantity: number
  price: string
  fee: string
  status: string
  reject_reason: string | null
  cancelable: boolean
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
  filled_quote_at: string | null
  created_at: string
}

export interface ReviewPosition {
  symbol: string
  stock_name: string
  quantity: number
  cost_basis: string
  price: string | null
  price_date: string | null
  price_source: 'SNAPSHOT' | 'HISTORY' | null
  market_value: string | null
  floating_pnl: string | null
}

export interface DailySnapshot {
  date: string
  cash: string
  market_value: string | null
  total_assets: string | null
  cost_basis: string
  realized_pnl: string
  floating_pnl: string | null
  cash_change: string
  market_value_change: string | null
  asset_change: string | null
  asset_change_delta: string | null
  trade_count: number
  trade_cash_flow: string
  fees: string
  ledger_cash_delta: string
  ledger_consistent: boolean
  reconciliation_delta: string | null
  valuation_status: 'COMPLETE' | 'CARRIED' | 'PROVISIONAL' | 'MISSING'
  missing_symbols: string[]
  positions: ReviewPosition[]
  calculated_at: string
}

export interface ReviewTrade {
  trade_no: string
  symbol: string
  stock_name: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: string
  amount: string
  fee: string
  cash_flow: string
  created_at: string
  note: string | null
}

export interface DayReview {
  snapshot: DailySnapshot
  trades: ReviewTrade[]
  daily_note: string | null
}

export interface WatchlistItem {
  symbol: string
  created_at: string
}

export interface StockBar {
  trade_date: string
  open_price: string
  high_price: string
  low_price: string
  close_price: string
  prev_close: string | null
  change: string | null
  change_percent: string | null
  volume: number
  turnover: string
}

export interface StockHistory {
  symbol: string
  period: 'DAY'
  source: 'EASTMONEY_HISTORY' | 'CACHED_HISTORY' | 'LATEST_SNAPSHOT'
  cached_count: number
  target_count: number
  complete: boolean
  bars: StockBar[]
}

export interface FinancialReport {
  report_period: string
  report_type: 'Q1' | 'H1' | 'Q3' | 'ANNUAL' | 'OTHER'
  report_name: string
  announcement_date: string
  revenue: string | null
  net_profit_parent: string | null
  basic_eps: string | null
  deducted_eps: string | null
  weighted_roe: string | null
  gross_margin: string | null
  revenue_yoy: string | null
  net_profit_yoy: string | null
  book_value_per_share: string | null
  operating_cash_flow_per_share: string | null
  source_url: string
}

export interface PerformanceEvent {
  event_type: 'FORECAST' | 'REPORT'
  event_label: string
  report_period: string
  report_name: string
  announcement_date: string
  forecast_type: string | null
  revenue_lower: string | null
  revenue_upper: string | null
  revenue_growth_lower: string | null
  revenue_growth_upper: string | null
  net_profit_lower: string | null
  net_profit_upper: string | null
  net_profit_growth_lower: string | null
  net_profit_growth_upper: string | null
  summary: string | null
  reason: string | null
  source_url: string
}

export interface StockFundamentals {
  symbol: string
  stock_name: string
  available: boolean
  provider: 'EASTMONEY'
  provider_label: string
  cache_status: 'REFRESHED' | 'CACHED' | 'STALE'
  fetched_at: string
  latest_report: FinancialReport
  reports: FinancialReport[]
  events: PerformanceEvent[]
}

export interface CompanyAnnouncement {
  external_id: string
  title: string
  announcement_date: string
  announcement_heading: string | null
  announcement_type: string | null
  exchange: 'SSE' | 'SZSE'
  source_url: string
  source_published_at: string | null
}

export interface StockAnnouncements {
  symbol: string
  stock_name: string
  provider: 'SSE' | 'SZSE'
  provider_label: string
  cache_status: 'REFRESHED' | 'CACHED' | 'STALE'
  fetched_at: string
  range_start: string
  range_end: string
  announcements: CompanyAnnouncement[]
}

export interface RegulatoryLetterReply {
  external_id: string
  title: string
  reply_date: string | null
  source_url: string
  match_method: 'SOURCE' | 'TITLE_DATE'
}

export interface RegulatoryLetter {
  external_id: string
  title: string
  letter_type: string
  issued_date: string
  exchange: 'SSE' | 'SZSE'
  source_url: string
  reply_status: 'REPLIED' | 'NO_REPLY_FOUND'
  replies: RegulatoryLetterReply[]
}

export interface StockRegulatoryLetters {
  symbol: string
  stock_name: string
  provider: 'SSE' | 'SZSE'
  provider_label: string
  cache_status: 'REFRESHED' | 'CACHED' | 'STALE'
  fetched_at: string
  range_start: string
  range_end: string
  letters: RegulatoryLetter[]
}

export interface OrderPreview {
  symbol: string
  side: 'BUY' | 'SELL'
  order_type: 'MARKET' | 'LIMIT'
  quantity: number
  reference_price: string
  limit_price: string | null
  estimated_amount: string
  estimated_fee: string
  estimated_total: string
  max_quantity: number
  position_quantity: number
  sellable_quantity: number
  frozen_sell_quantity: number
  post_available_cash: string
  post_position_ratio: string
  price_limit_rate: string
  upper_limit: string
  lower_limit: string
  quote_updated_at: string
  quote_source_at: string | null
  allowed: boolean
  blocking_reason: string | null
  warnings: string[]
}

export interface OrderRequest {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  order_type: 'MARKET' | 'LIMIT'
  limit_price?: number
}
export interface CurrentUser {
  username: string
  initial_cash: string
  created_at: string
}

export interface AuthRequest {
  username: string
  password: string
  invite_code?: string
}

export interface StockAnalysisRequest {
  question: string
  focus_keywords?: string
  history_days?: number
  disclosure_days?: number
  news_days?: number
  max_documents?: number
}

export interface AgentEvidence {
  evidence_key: string
  tool_name: string
  evidence_type: string
  title: string | null
  source_url: string | null
  source_date: string | null
  source_published_at: string | null
  page_number: number | null
  start_char: number | null
  end_char: number | null
  excerpt: string
  content_hash: string | null
  trust_level: string
  metadata: Record<string, unknown>
}

export type AgentRiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'
export type AgentAction =
  | 'WATCH'
  | 'HOLD'
  | 'AVOID'
  | 'REDUCE'
  | 'CONSIDER_ADD'
  | 'NO_CONCLUSION'

export interface AgentRecommendation {
  risk_level: AgentRiskLevel
  confidence: string
  action: AgentAction
  time_horizon: string | null
  summary: string
  reasoning: string
  positive_factors: string[]
  risk_factors: string[]
  action_conditions: string[]
  invalidation_conditions: string[]
  evidence_keys: string[]
  disclaimer: string
  output_hash: string
}

export interface AgentRun {
  id: number
  run_type: string
  status: 'RUNNING' | 'COMPLETED' | 'FAILED'
  symbol: string | null
  stock_name: string | null
  model_provider: string
  model_name: string
  request_text: string | null
  data_as_of: string | null
  input_tokens: number
  output_tokens: number
  total_tokens: number
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  evidence: AgentEvidence[]
  recommendation: AgentRecommendation | null
}
