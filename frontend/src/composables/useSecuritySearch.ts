import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import type { SecuritySearchResult } from '../types'

export function useSecuritySearch() {
  const keyword = ref('')
  const result = ref<SecuritySearchResult | null>(null)
  const loading = ref(false)
  const error = ref('')
  let generation = 0
  let disposed = false
  let timer: number | undefined

  async function refresh() {
    if (disposed) return
    window.clearTimeout(timer)
    const id = ++generation
    loading.value = true
    error.value = ''
    try {
      const response = await api.searchSecurities(keyword.value.trim())
      if (!disposed && id === generation) result.value = response
    } catch (reason) {
      if (!disposed && id === generation) {
        result.value = null
        error.value = reason instanceof Error ? reason.message : '证券目录暂时无法读取'
      }
    } finally {
      if (!disposed && id === generation) loading.value = false
    }
  }

  watch(keyword, () => {
    generation++
    result.value = null
    error.value = ''
    loading.value = true
    window.clearTimeout(timer)
    timer = window.setTimeout(() => void refresh(), 300)
  }, { flush: 'sync' })

  onBeforeUnmount(() => { disposed = true; generation++; window.clearTimeout(timer) })
  return { keyword, result, loading, error, refresh }
}
