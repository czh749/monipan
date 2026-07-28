import type { Account, MarketIndex, MarketStatus, Order, Position, Stock, Trade } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg).join('；')
      : detail || '请求失败，请稍后重试'
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  stocks: () => request<Stock[]>('/api/stocks'),
  marketStatus: () => request<MarketStatus>('/api/market/status'),
  marketIndices: () => request<MarketIndex[]>('/api/market/indices'),
  account: () => request<Account>('/api/account'),
  positions: () => request<Position[]>('/api/positions'),
  orders: () => request<Order[]>('/api/orders'),
  trades: () => request<Trade[]>('/api/trades'),
  placeOrder: (symbol: string, side: 'BUY' | 'SELL', quantity: number) =>
    request<Order>('/api/orders', {
      method: 'POST',
      body: JSON.stringify({ symbol, side, quantity }),
    }),
}
