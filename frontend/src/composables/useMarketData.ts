import { ref } from 'vue'
import { api } from '../api'
import type { MarketIndex, MarketStatus, Stock } from '../types'

const MARKET_POLL_INTERVAL_MS = 15_000

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : '无法连接行情服务'
}

export function useMarketData() {
  const stocks = ref<Stock[]>([])
  const marketStatus = ref<MarketStatus | null>(null)
  const marketIndices = ref<MarketIndex[]>([])
  const loading = ref(true)
  const error = ref('')

  let timer: number | undefined
  let stopped = true
  let refreshPromise: Promise<void> | null = null
  let generation = 0

  async function performRefresh() {
    const id = generation
    const results = await Promise.allSettled([
      api.stocks(),
      api.marketStatus(),
      api.marketIndices(),
    ])
    const [stockResult, statusResult, indexResult] = results
    if (id !== generation) return

    if (stockResult.status === 'fulfilled') stocks.value = stockResult.value
    if (statusResult.status === 'fulfilled') marketStatus.value = statusResult.value
    if (indexResult.status === 'fulfilled') marketIndices.value = indexResult.value

    const failure = results.find((result) => result.status === 'rejected')
    error.value = failure?.status === 'rejected' ? errorMessage(failure.reason) : ''
    loading.value = false
  }

  function refreshMarket() {
    if (refreshPromise) return refreshPromise
    const job: Promise<void> = performRefresh().finally(() => {
      if (refreshPromise === job) refreshPromise = null
    })
    refreshPromise = job
    return job
  }

  function scheduleNextRefresh() {
    window.clearTimeout(timer)
    if (!stopped) timer = window.setTimeout(runPollingCycle, MARKET_POLL_INTERVAL_MS)
  }

  async function runPollingCycle() {
    if (stopped) return
    if (document.visibilityState === 'visible') await refreshMarket()
    scheduleNextRefresh()
  }

  function handleVisibilityChange() {
    if (document.visibilityState !== 'visible' || stopped) return
    window.clearTimeout(timer)
    void runPollingCycle()
  }

  async function startMarketPolling() {
    if (!stopped) return
    stopped = false
    document.addEventListener('visibilitychange', handleVisibilityChange)
    await refreshMarket()
    scheduleNextRefresh()
  }

  function stopMarketPolling() {
    generation++
    refreshPromise = null
    stocks.value = []
    marketStatus.value = null
    marketIndices.value = []
    error.value = ''
    loading.value = true
    stopped = true
    window.clearTimeout(timer)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }

  return {
    stocks,
    marketStatus,
    marketIndices,
    loading,
    error,
    refreshMarket,
    startMarketPolling,
    stopMarketPolling,
  }
}
