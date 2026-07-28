<script setup lang="ts">
import { computed } from 'vue'
import type { Account, MarketStatus, Position, Stock } from '../types'
import {
  formatNumber,
  fullDateTime,
  quoteNumber,
  relativeTime,
  riseClass,
  shortDateTime,
} from '../utils/formatters'

const props = defineProps<{
  stock?: Stock
  marketStatus: MarketStatus | null
  account: Account | null
  position?: Position
  side: 'BUY' | 'SELL'
  quantity: number
  submitting: boolean
  currentTime: number
}>()

const emit = defineEmits<{
  'update:side': [side: 'BUY' | 'SELL']
  'update:quantity': [quantity: number]
  submit: []
}>()

const estimatedAmount = computed(() =>
  props.stock ? Number(props.stock.price) * props.quantity : 0,
)

const priceRangePosition = computed(() => {
  if (!props.stock) return 50
  const low = Number(props.stock.low_price)
  const high = Number(props.stock.high_price)
  const price = Number(props.stock.price)
  if (high <= low) return 50
  return Math.min(100, Math.max(0, (price - low) / (high - low) * 100))
})

const hasDayRange = computed(() =>
  Boolean(
    props.stock &&
    Number(props.stock.low_price) > 0 &&
    Number(props.stock.high_price) > 0,
  ),
)

function setQuantity(quantity: number) {
  emit('update:quantity', Math.max(100, quantity))
}
</script>

<template>
  <aside class="panel order-panel">
    <template v-if="stock">
      <div class="ticket-label"><span>ORDER TICKET</span><strong>市价委托</strong></div>
      <div class="stock-head">
        <div><span>{{ stock.symbol }}</span><h2>{{ stock.name }}</h2><small>{{ stock.industry }}</small></div>
        <div class="stock-price" :class="riseClass(stock.change)">
          <strong>{{ formatNumber(stock.price) }}</strong>
          <span>{{ Number(stock.change) >= 0 ? '+' : '' }}{{ formatNumber(stock.change) }} / {{ formatNumber(stock.change_percent) }}%</span>
        </div>
      </div>
      <div class="quote-provenance">
        <span class="status-dot"></span>
        <strong>{{ marketStatus?.provider_label ?? '东方财富' }} · 真实行情</strong>
        <time :datetime="stock.updated_at" :title="fullDateTime(stock.updated_at)">
          更新于 {{ shortDateTime(stock.updated_at) }}（{{ relativeTime(stock.updated_at, currentTime) }}）
        </time>
      </div>
      <div class="quote-strip">
        <span>昨收<strong>{{ formatNumber(stock.prev_close) }}</strong></span>
        <span>最高<strong :class="Number(stock.high_price) > 0 ? 'rise' : 'muted'">{{ quoteNumber(stock.high_price) }}</strong></span>
        <span>最低<strong :class="Number(stock.low_price) > 0 ? 'fall' : 'muted'">{{ quoteNumber(stock.low_price) }}</strong></span>
      </div>
      <div v-if="hasDayRange" class="day-range">
        <div><span>日内价格位置</span><strong>{{ formatNumber(stock.low_price) }} — {{ formatNumber(stock.high_price) }}</strong></div>
        <div class="range-track"><i :style="{ left: `${priceRangePosition}%` }"></i></div>
        <div class="range-labels"><span>LOW</span><b>{{ formatNumber(priceRangePosition) }}%</b><span>HIGH</span></div>
      </div>
      <div v-else class="day-range-pending">
        <span>OPEN DATA PENDING</span>
        <div><strong>等待开盘数据</strong><small>今开、最高和最低将在数据源形成后显示</small></div>
      </div>
      <div class="side-switch">
        <button :class="{ active: side === 'BUY' }" @click="emit('update:side', 'BUY')">买入</button>
        <button :class="{ active: side === 'SELL' }" @click="emit('update:side', 'SELL')">卖出</button>
      </div>
      <div class="form-group">
        <label>委托方式</label>
        <div class="readonly-field">市价委托 <span>即时成交</span></div>
      </div>
      <div class="form-group">
        <label>委托数量 <small>100股/手</small></label>
        <div class="stepper">
          <button @click="setQuantity(quantity - 100)">−</button>
          <input
            :value="quantity"
            type="number"
            min="100"
            step="100"
            @input="setQuantity(Number(($event.target as HTMLInputElement).value))"
          />
          <button @click="setQuantity(quantity + 100)">＋</button>
        </div>
        <div class="quick-amounts">
          <button v-for="value in [100, 500, 1000, 2000]" :key="value" @click="setQuantity(value)">
            {{ value }}
          </button>
        </div>
      </div>
      <div class="order-review">
        <span>预估金额<strong>¥ {{ formatNumber(estimatedAmount) }}</strong></span>
        <span v-if="side === 'BUY'">可用资金<strong>¥ {{ formatNumber(account?.available_cash ?? 0) }}</strong></span>
        <span v-else>可卖数量<strong>{{ position?.quantity ?? 0 }} 股</strong></span>
      </div>
      <button
        class="submit-order"
        :class="side === 'BUY' ? 'buy-button' : 'sell-button'"
        :disabled="submitting"
        @click="emit('submit')"
      >
        {{ submitting ? '提交中…' : `${side === 'BUY' ? '买入' : '卖出'} ${stock.name}` }}
      </button>
      <p class="trade-note">模拟交易，仅供学习。成交价以提交时的模拟最新价为准。</p>
    </template>
  </aside>
</template>
