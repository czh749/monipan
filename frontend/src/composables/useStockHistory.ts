import { onBeforeUnmount, ref } from 'vue'
import { api } from '../api'
import type { StockHistory } from '../types'

export function useStockHistory(options: { isOpen: () => boolean; currentSymbol: () => string | undefined }) {
  const history = ref<StockHistory | null>(null)
  const loading = ref(false)
  const error = ref('')
  let requestId = 0
  let timer: ReturnType<typeof window.setTimeout> | undefined

  function resetHistory() {
    requestId += 1
    if (timer !== undefined) window.clearTimeout(timer)
    history.value = null
    loading.value = false
    error.value = ''
  }

  async function loadHistory(symbol: string, refresh = true) {
    const currentRequest = ++requestId
    if (timer !== undefined) window.clearTimeout(timer)
    loading.value = refresh || !history.value
    error.value = ''
    try {
      const result = await api.stockHistory(symbol, refresh)
      if (currentRequest === requestId) history.value = result
    } catch (reason) {
      if (currentRequest === requestId) {
        error.value = reason instanceof Error ? reason.message : '无法加载历史行情'
      }
    } finally {
      if (currentRequest === requestId) {
        loading.value = false
        if (options.isOpen() && options.currentSymbol() === symbol) {
          // Automatic refresh reads the local cache; only explicit loads fetch.
          timer = window.setTimeout(() => { void loadHistory(symbol, false) }, 30_000)
        }
      }
    }
  }

  onBeforeUnmount(resetHistory)
  return { history, loading, error, loadHistory, resetHistory }
}
