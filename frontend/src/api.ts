import type {
  Account,
  MarketIndex,
  MarketStatus,
  Order,
  OrderPreview,
  OrderRequest,
  Position,
  Stock,
  StockAnnouncements,
  StockFundamentals,
  StockHistory,
  StockRegulatoryLetters,
  Trade,
  DailySnapshot,
  DayReview,
  WatchlistItem,
  AuthRequest,
  CurrentUser,
  AgentRun,
  StockAnalysisRequest,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly runId?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg).join('；')
      : typeof detail === 'object' && detail?.message
        ? detail.message
        : typeof detail === 'string'
          ? detail
          : '请求失败，请稍后重试'
    throw new ApiError(message, response.status, detail?.code, detail?.run_id)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  currentUser: () => request<CurrentUser>('/api/auth/me'),
  register: (payload: AuthRequest) =>
    request<CurrentUser>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  login: (payload: AuthRequest) =>
    request<CurrentUser>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  stocks: () => request<Stock[]>('/api/stocks'),
  marketStatus: () => request<MarketStatus>('/api/market/status'),
  marketIndices: () => request<MarketIndex[]>('/api/market/indices'),
  account: () => request<Account>('/api/account'),
  positions: () => request<Position[]>('/api/positions'),
  orders: () => request<Order[]>('/api/orders'),
  trades: () => request<Trade[]>('/api/trades'),
  reviewSnapshots: (days = 90) => request<DailySnapshot[]>(`/api/review/snapshots?days=${days}`),
  dayReview: (date: string) => request<DayReview>(`/api/review/days/${date}`),
  saveDayReviewNote: (date: string, content: string) =>
    request<{ content: string | null }>(`/api/review/days/${date}/note`, {
      method: 'PUT', body: JSON.stringify({ content }),
    }),
  saveTradeNote: (tradeNo: string, content: string) =>
    request<{ content: string | null }>(`/api/review/trades/${tradeNo}/note`, {
      method: 'PUT', body: JSON.stringify({ content }),
    }),
  watchlist: () => request<WatchlistItem[]>('/api/watchlist'),
  addWatchlist: (symbol: string) => request<WatchlistItem>(`/api/watchlist/${symbol}`, { method: 'POST' }),
  removeWatchlist: (symbol: string) => request<void>(`/api/watchlist/${symbol}`, { method: 'DELETE' }),
  stockHistory: (symbol: string) => request<StockHistory>(`/api/stocks/${symbol}/history`),
  stockFundamentals: (symbol: string) =>
    request<StockFundamentals>(`/api/stocks/${symbol}/fundamentals`),
  stockAnnouncements: (symbol: string, days = 90, limit = 20) =>
    request<StockAnnouncements>(
      `/api/stocks/${symbol}/announcements?days=${days}&limit=${limit}`,
    ),
  stockRegulatoryLetters: (symbol: string, days = 90, limit = 20) =>
    request<StockRegulatoryLetters>(
      `/api/stocks/${symbol}/regulatory-letters?days=${days}&limit=${limit}`,
    ),
  analyzeStock: (symbol: string, payload: StockAnalysisRequest) =>
    request<AgentRun>(`/api/agent/stocks/${symbol}/analyze`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  agentRun: (runId: number) => request<AgentRun>(`/api/agent/runs/${runId}`),
  previewOrder: (payload: OrderRequest) =>
    request<OrderPreview>('/api/orders/preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  placeOrder: (payload: OrderRequest, idempotencyKey: string) =>
    request<Order>('/api/orders', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
  cancelOrder: (orderNo: string) =>
    request<Order>(`/api/orders/${orderNo}/cancel`, { method: 'POST' }),
}
