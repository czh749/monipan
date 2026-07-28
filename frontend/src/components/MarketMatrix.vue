<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Stock } from '../types'
import {
  compactVolume,
  formatNumber,
  quoteNumber,
  riseClass,
} from '../utils/formatters'

const props = defineProps<{
  stocks: Stock[]
  selectedSymbol: string
  totalCount: number
  loading: boolean
}>()

const emit = defineEmits<{
  select: [stock: Stock]
}>()

const search = ref('')
const filteredStocks = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return props.stocks
  return props.stocks.filter(
    (stock) =>
      stock.symbol.includes(keyword) ||
      stock.name.toLowerCase().includes(keyword) ||
      stock.industry.toLowerCase().includes(keyword),
  )
})
</script>

<template>
  <article class="panel market-panel">
    <div class="panel-header">
      <div>
        <span class="section-kicker">MARKET MATRIX</span>
        <h2>行情矩阵</h2>
        <span>扫描价格、涨跌和成交量，点击一行装载订单票据</span>
      </div>
      <label class="search-box">
        <span class="search-icon">⌕</span>
        <input v-model="search" placeholder="搜索代码、名称或行业" />
        <kbd>{{ filteredStocks.length }} / {{ totalCount }}</kbd>
      </label>
    </div>
    <div class="table-wrap market-table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th><th>最新价</th><th>涨跌幅</th><th>今开</th>
            <th>最高</th><th>最低</th><th>成交量</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="stock in filteredStocks"
            :key="stock.symbol"
            :class="{ selected: selectedSymbol === stock.symbol }"
            @click="emit('select', stock)"
          >
            <td><strong>{{ stock.name }}</strong><small>{{ stock.symbol }} · {{ stock.exchange }}</small></td>
            <td :class="riseClass(stock.change)"><strong>{{ formatNumber(stock.price) }}</strong></td>
            <td :class="riseClass(stock.change)">{{ Number(stock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(stock.change_percent) }}%</td>
            <td :class="{ muted: Number(stock.open_price) <= 0 }">{{ quoteNumber(stock.open_price) }}</td>
            <td :class="Number(stock.high_price) > 0 ? 'rise' : 'muted'">{{ quoteNumber(stock.high_price) }}</td>
            <td :class="Number(stock.low_price) > 0 ? 'fall' : 'muted'">{{ quoteNumber(stock.low_price) }}</td>
            <td>{{ compactVolume(stock.volume) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="loading" class="loading-state"><span></span>正在载入模拟市场…</div>
  </article>
</template>
