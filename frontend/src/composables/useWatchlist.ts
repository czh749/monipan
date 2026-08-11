import { computed, ref } from 'vue'
import { api } from '../api'

export function useWatchlist() {
  const symbols = ref<string[]>([])
  const loading = ref(false)
  const error = ref('')
  const symbolSet = computed(() => new Set(symbols.value))

  async function loadWatchlist() {
    loading.value = true
    try {
      symbols.value = (await api.watchlist()).map((item) => item.symbol)
      error.value = ''
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '无法加载自选股'
    } finally {
      loading.value = false
    }
  }

  async function toggleWatchlist(symbol: string) {
    const removing = symbolSet.value.has(symbol)
    try {
      if (removing) {
        await api.removeWatchlist(symbol)
        symbols.value = symbols.value.filter((item) => item !== symbol)
      } else {
        await api.addWatchlist(symbol)
        symbols.value = [symbol, ...symbols.value]
      }
      error.value = ''
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '无法更新自选股'
      throw reason
    }
  }

  function resetWatchlist() {
    symbols.value = []
    error.value = ''
    loading.value = false
  }

  return { symbols, symbolSet, loading, error, loadWatchlist, toggleWatchlist, resetWatchlist }
}
