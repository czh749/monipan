import { computed, ref } from 'vue'
import { api } from '../api'
import type { Account, Order, OrderRequest, Position, Trade } from '../types'

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : '无法加载交易数据'
}

interface PendingOrderSubmission {
  fingerprint: string
  idempotencyKey: string
}

const PENDING_ORDER_STORAGE_KEY = 'monipan.pending-order-submission'
let memoryPendingOrder: PendingOrderSubmission | null = null

function orderFingerprint(payload: OrderRequest) {
  return JSON.stringify({
    symbol: payload.symbol,
    side: payload.side,
    quantity: payload.quantity,
    orderType: payload.order_type,
    limitPrice: payload.order_type === 'LIMIT'
      ? Number(payload.limit_price).toFixed(2)
      : null,
  })
}

function readPendingOrder() {
  if (memoryPendingOrder) return memoryPendingOrder
  try {
    const raw = sessionStorage.getItem(PENDING_ORDER_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as PendingOrderSubmission
    if (!parsed.fingerprint || !parsed.idempotencyKey) return null
    memoryPendingOrder = parsed
    return parsed
  } catch {
    return null
  }
}

function writePendingOrder(value: PendingOrderSubmission | null) {
  memoryPendingOrder = value
  try {
    if (value) {
      sessionStorage.setItem(PENDING_ORDER_STORAGE_KEY, JSON.stringify(value))
    } else {
      sessionStorage.removeItem(PENDING_ORDER_STORAGE_KEY)
    }
  } catch {
    // The in-memory copy still protects retries when browser storage is blocked.
  }
}

function idempotencyKeyFor(payload: OrderRequest) {
  const fingerprint = orderFingerprint(payload)
  const pending = readPendingOrder()
  if (pending?.fingerprint === fingerprint) return pending.idempotencyKey
  const next = { fingerprint, idempotencyKey: crypto.randomUUID() }
  writePendingOrder(next)
  return next.idempotencyKey
}

export function useTrading() {
  const account = ref<Account | null>(null)
  const positions = ref<Position[]>([])
  const orders = ref<Order[]>([])
  const trades = ref<Trade[]>([])
  const loading = ref(true)
  const submitting = ref(false)
  const portfolioError = ref('')
  const historyError = ref('')
  const error = computed(() => portfolioError.value || historyError.value)

  async function refreshPortfolio() {
    try {
      const [accountData, positionData] = await Promise.all([
        api.account(),
        api.positions(),
      ])
      account.value = accountData
      positions.value = positionData
      portfolioError.value = ''
    } catch (reason) {
      portfolioError.value = errorMessage(reason)
    }
  }

  async function refreshHistory() {
    try {
      const [orderData, tradeData] = await Promise.all([
        api.orders(),
        api.trades(),
      ])
      orders.value = orderData
      trades.value = tradeData
      historyError.value = ''
    } catch (reason) {
      historyError.value = errorMessage(reason)
    }
  }

  async function initializeTrading() {
    await Promise.all([refreshPortfolio(), refreshHistory()])
    loading.value = false
  }

  async function placeOrder(payload: OrderRequest) {
    if (submitting.value) return null
    submitting.value = true
    const idempotencyKey = idempotencyKeyFor(payload)
    try {
      const order = await api.placeOrder(payload, idempotencyKey)
      writePendingOrder(null)
      await Promise.all([refreshPortfolio(), refreshHistory()])
      return order
    } finally {
      submitting.value = false
    }
  }

  async function cancelOrder(orderNo: string) {
    const order = await api.cancelOrder(orderNo)
    await Promise.all([refreshPortfolio(), refreshHistory()])
    return order
  }

  function clearError() {
    portfolioError.value = ''
    historyError.value = ''
  }

  function resetTrading() {
    account.value = null
    positions.value = []
    orders.value = []
    trades.value = []
    loading.value = true
    clearError()
  }

  return {
    account,
    positions,
    orders,
    trades,
    loading,
    submitting,
    error,
    initializeTrading,
    refreshPortfolio,
    refreshHistory,
    placeOrder,
    cancelOrder,
    clearError,
    resetTrading,
  }
}
