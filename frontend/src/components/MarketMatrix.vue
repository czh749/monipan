<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Stock } from '../types'
import { compactVolume, formatNumber, quoteNumber, riseClass } from '../utils/formatters'

type MarketView = 'all' | 'watchlist' | 'positions' | 'gainers'
type SortKey = 'symbol' | 'price' | 'change_percent' | 'volume'

const props = defineProps<{
  stocks: Stock[]
  selectedSymbol: string
  totalCount: number
  loading: boolean
  watchlistSymbols: string[]
  positionSymbols: string[]
}>()

const emit = defineEmits<{
  select: [stock: Stock]
  toggleWatchlist: [symbol: string]
  showDetails: [stock: Stock]
}>()

const search = ref('')
const view = ref<MarketView>('all')
const industry = ref('')
const sortKey = ref<SortKey>('symbol')
const sortDirection = ref<'asc' | 'desc'>('asc')

const watchlist = computed(() => new Set(props.watchlistSymbols))
const positions = computed(() => new Set(props.positionSymbols))
const industries = computed(() =>
  [...new Set(props.stocks.map((stock) => stock.industry).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN')),
)

const filteredStocks = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const result = props.stocks.filter((stock) => {
    const matchesKeyword = !keyword || stock.symbol.includes(keyword) ||
      stock.name.toLowerCase().includes(keyword) || stock.industry.toLowerCase().includes(keyword)
    const matchesIndustry = !industry.value || stock.industry === industry.value
    const matchesView = view.value === 'all' ||
      (view.value === 'watchlist' && watchlist.value.has(stock.symbol)) ||
      (view.value === 'positions' && positions.value.has(stock.symbol)) ||
      (view.value === 'gainers' && Number(stock.change_percent) > 0)
    return matchesKeyword && matchesIndustry && matchesView
  })
  return result.sort((left, right) => {
    const a = sortKey.value === 'symbol' ? left.symbol : Number(left[sortKey.value])
    const b = sortKey.value === 'symbol' ? right.symbol : Number(right[sortKey.value])
    const comparison = typeof a === 'string' ? a.localeCompare(String(b)) : a - Number(b)
    return sortDirection.value === 'asc' ? comparison : -comparison
  })
})

function setSort(key: SortKey) {
  if (sortKey.value === key) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  else {
    sortKey.value = key
    sortDirection.value = key === 'symbol' ? 'asc' : 'desc'
  }
}

function sortMark(key: SortKey) {
  return sortKey.value === key ? (sortDirection.value === 'asc' ? '↑' : '↓') : ''
}
</script>

<template>
  <article class="panel market-panel">
    <div class="panel-header market-panel-header">
      <div>
        <span class="section-kicker">MARKET SCANNER</span>
        <h2>行情扫描台</h2>
        <span>筛选、自选与排序后装载订单票据；双击查看价格结构</span>
      </div>
      <label class="search-box">
        <span class="search-icon">⌕</span>
        <input v-model="search" placeholder="搜索代码、名称或行业" />
        <kbd>{{ filteredStocks.length }} / {{ totalCount }}</kbd>
      </label>
    </div>

    <div class="market-toolbar" aria-label="行情筛选">
      <div class="market-view-tabs">
        <button :class="{ active: view === 'all' }" @click="view = 'all'">全部</button>
        <button :class="{ active: view === 'watchlist' }" @click="view = 'watchlist'">自选 {{ watchlistSymbols.length }}</button>
        <button :class="{ active: view === 'positions' }" @click="view = 'positions'">持仓 {{ positionSymbols.length }}</button>
        <button :class="{ active: view === 'gainers' }" @click="view = 'gainers'">上涨</button>
      </div>
      <label class="industry-filter">
        <span>行业</span>
        <select v-model="industry">
          <option value="">全部行业</option>
          <option v-for="item in industries" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
    </div>

    <div class="table-wrap market-table-wrap">
      <table>
        <thead>
          <tr>
            <th class="watch-column" aria-label="自选"></th>
            <th><button class="sort-button" @click="setSort('symbol')">股票 {{ sortMark('symbol') }}</button></th>
            <th><button class="sort-button" @click="setSort('price')">最新价 {{ sortMark('price') }}</button></th>
            <th><button class="sort-button" @click="setSort('change_percent')">涨跌幅 {{ sortMark('change_percent') }}</button></th>
            <th>今开</th><th>最高</th><th>最低</th>
            <th><button class="sort-button" @click="setSort('volume')">成交量 {{ sortMark('volume') }}</button></th>
            <th class="research-column">研究</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="stock in filteredStocks"
            :key="stock.symbol"
            :class="{ selected: selectedSymbol === stock.symbol }"
            @click="emit('select', stock)"
            @dblclick="emit('showDetails', stock)"
          >
            <td class="watch-column">
              <button
                class="watch-button"
                :class="{ active: watchlist.has(stock.symbol) }"
                :aria-label="watchlist.has(stock.symbol) ? '移出自选' : '加入自选'"
                @click.stop="emit('toggleWatchlist', stock.symbol)"
              >{{ watchlist.has(stock.symbol) ? '★' : '☆' }}</button>
            </td>
            <td><strong>{{ stock.name }}</strong><small>{{ stock.symbol }} · {{ stock.exchange }} · {{ stock.industry }}</small></td>
            <td :class="riseClass(stock.change)"><strong>{{ formatNumber(stock.price) }}</strong></td>
            <td :class="riseClass(stock.change)">{{ Number(stock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(stock.change_percent) }}%</td>
            <td :class="{ muted: Number(stock.open_price) <= 0 }">{{ quoteNumber(stock.open_price) }}</td>
            <td :class="Number(stock.high_price) > 0 ? 'rise' : 'muted'">{{ quoteNumber(stock.high_price) }}</td>
            <td :class="Number(stock.low_price) > 0 ? 'fall' : 'muted'">{{ quoteNumber(stock.low_price) }}</td>
            <td>{{ compactVolume(stock.volume) }}</td>
            <td class="research-column">
              <button
                class="chart-action"
                :aria-label="`查看${stock.name}的行情、财务和业绩详情`"
                @click.stop="emit('showDetails', stock)"
              >
                <strong>详情</strong>
                <small>行情 · 财务 · 业绩</small>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && !filteredStocks.length" class="empty-state compact-empty">
        <span>◇</span><strong>当前筛选没有股票</strong><small>调整自选、持仓或行业条件后再试</small>
      </div>
    </div>
    <div v-if="loading" class="loading-state"><span></span>正在载入模拟市场…</div>
  </article>
</template>
