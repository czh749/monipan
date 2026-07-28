import { computed, ref } from 'vue'
import { api } from '../api'
import type { Account, Order, Position, Trade } from '../types'

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : '无法加载交易数据'
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

  async function placeOrder(
    symbol: string,
    side: 'BUY' | 'SELL',
    quantity: number,
  ) {
    if (submitting.value) return null
    submitting.value = true
    try {
      const order = await api.placeOrder(symbol, side, quantity)
      await Promise.all([refreshPortfolio(), refreshHistory()])
      return order
    } finally {
      submitting.value = false
    }
  }

  function clearError() {
    portfolioError.value = ''
    historyError.value = ''
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
    clearError,
  }
}
