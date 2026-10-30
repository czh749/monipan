import { computed, ref } from 'vue'
import { api } from '../api'

export function useWatchlist() {
  const symbols = ref<string[]>([])
  const loading = ref(false)
  const error = ref('')
  const symbolSet = computed(() => new Set(symbols.value))
  let generation = 0
  let loadId = 0
  const pending = new Map<string, Promise<void>>()

  async function loadWatchlist() {
    const epoch = generation
    const id = ++loadId
    loading.value = true
    try {
      const items = await api.watchlist()
      if (epoch !== generation || id !== loadId) return
      symbols.value = items.map((item) => item.symbol)
      error.value = ''
    } catch (reason) {
      if (epoch !== generation || id !== loadId) return
      error.value = reason instanceof Error ? reason.message : '无法加载自选股'
    } finally {
      if (epoch === generation && id === loadId) loading.value = false
    }
  }

  function toggleWatchlist(symbol: string): Promise<void> {
    if (pending.has(symbol)) return pending.get(symbol)!
    const epoch = generation
    const job = updateWatchlist(symbol, epoch).finally(() => {
      if (pending.get(symbol) === job) pending.delete(symbol)
    })
    pending.set(symbol, job)
    return job
  }

  async function updateWatchlist(symbol: string, epoch: number) {
    loadId++
    loading.value = false
    const removing = symbolSet.value.has(symbol)
    try {
      if (removing) {
        await api.removeWatchlist(symbol)
        if (epoch !== generation) return
        symbols.value = symbols.value.filter((item) => item !== symbol)
      } else {
        await api.addWatchlist(symbol)
        if (epoch !== generation) return
        symbols.value = [symbol, ...symbols.value]
      }
      error.value = ''
    } catch (reason) {
      if (epoch !== generation) return
      error.value = reason instanceof Error ? reason.message : '无法更新自选股'
      throw reason
    }
  }

  function resetWatchlist() {
    generation++
    loadId++
    pending.clear()
    symbols.value = []
    error.value = ''
    loading.value = false
  }

  return { symbols, symbolSet, loading, error, loadWatchlist, toggleWatchlist, resetWatchlist }
}
