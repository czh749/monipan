<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useSecuritySearch } from '../composables/useSecuritySearch'
import { fullDateTime } from '../utils/formatters'

const props = defineProps<{
  watchlistSymbols: string[]
  changeWatchlist: (symbol: string) => Promise<void>
}>()
const emit = defineEmits<{ open: [symbol: string] }>()
const { keyword, result, loading, error, refresh } = useSecuritySearch()
const pending = ref('')
const actionError = ref('')

async function change(symbol: string) {
  if (pending.value) return
  pending.value = symbol
  actionError.value = ''
  try {
    await props.changeWatchlist(symbol)
    await refresh()
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '添加失败，请重试'
  } finally { pending.value = '' }
}
watch(() => props.watchlistSymbols, () => void refresh())
onMounted(() => void refresh())
</script>

<template>
  <section class="panel security-explorer" aria-label="搜索更多沪深股票">
    <div class="security-search-head">
      <div><h2>搜索更多沪深股票</h2><p>加入自选后可查看行情、财务与公告；池外股票首版仅供研究。</p></div>
      <span class="research-quota">池外研究 {{ result?.quota.used ?? '—' }} / {{ result?.quota.limit ?? 30 }}</span>
    </div>
    <div class="security-search-controls">
      <label class="search-box"><span aria-hidden="true">⌕</span><input v-model="keyword" maxlength="40" placeholder="输入 A 股代码或公司名称" aria-label="全市场证券搜索" /></label>
      <button class="chart-action" :disabled="loading" @click="refresh">{{ loading ? '查询中…' : '重新查询' }}</button>
    </div>
    <p v-if="error || actionError" role="alert" class="security-message">{{ error || actionError }}</p>
    <template v-if="result">
      <p v-if="result.catalog.state === 'empty'" class="security-message">证券目录尚未初始化，请管理员同步交易所目录。默认股票池仍可使用。</p>
      <p v-else class="security-source">沪深交易所目录 · {{ result.catalog.count }} 只 · 采集于 {{ fullDateTime(result.catalog.collected_at) }}<span v-if="result.catalog.state === 'stale'" class="research-badge">目录超过 7 天，更新后可添加</span></p>
      <ul v-if="result.items.length" class="security-results">
        <li v-for="item in result.items" :key="`${item.exchange}:${item.symbol}`">
          <div><strong>{{ item.name }}</strong><span v-if="item.research_only" class="research-badge">仅研究</span><small>{{ item.symbol }} · {{ item.exchange }} · {{ item.board }} · 交易状态未核实</small><a :href="item.source_url" target="_blank" rel="noopener noreferrer">交易所来源</a></div>
          <div class="security-actions">
            <button v-if="watchlistSymbols.includes(item.symbol)" class="chart-action" @click="emit('open', item.symbol)">查看研究</button>
            <button class="chart-action" :disabled="Boolean(pending) || (!watchlistSymbols.includes(item.symbol) && (result.catalog.state !== 'ready' || (item.research_only && result.quota.used >= result.quota.limit)))" @click="change(item.symbol)">{{ pending === item.symbol ? '处理中…' : watchlistSymbols.includes(item.symbol) ? '移除' : '加入自选' }}</button>
          </div>
        </li>
      </ul>
      <p v-else-if="keyword.trim() && !loading && result.catalog.state !== 'empty'" class="security-source">没有匹配的沪深 A 股，请尝试完整代码或简称。</p>
    </template>
  </section>
</template>
