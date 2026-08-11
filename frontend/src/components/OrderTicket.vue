<script setup lang="ts">
import { computed } from 'vue'
import type { Account, MarketStatus, OrderPreview, Position, Stock } from '../types'
import { formatNumber, fullDateTime, quoteNumber, relativeTime, riseClass, shortDateTime } from '../utils/formatters'

const props = defineProps<{
  stock?: Stock
  marketStatus: MarketStatus | null
  account: Account | null
  position?: Position
  side: 'BUY' | 'SELL'
  orderType: 'MARKET' | 'LIMIT'
  limitPrice: number
  quantity: number
  preview: OrderPreview | null
  previewLoading: boolean
  submitting: boolean
  currentTime: number
}>()

const emit = defineEmits<{
  'update:side': [side: 'BUY' | 'SELL']
  'update:orderType': [type: 'MARKET' | 'LIMIT']
  'update:limitPrice': [price: number]
  'update:quantity': [quantity: number]
  submit: []
}>()

const estimatedAmount = computed(() => Number(props.preview?.estimated_amount ?? 0))
const priceRangePosition = computed(() => {
  if (!props.stock) return 50
  const low = Number(props.stock.low_price)
  const high = Number(props.stock.high_price)
  const price = Number(props.stock.price)
  if (high <= low) return 50
  return Math.min(100, Math.max(0, (price - low) / (high - low) * 100))
})
const hasDayRange = computed(() => Boolean(props.stock && Number(props.stock.low_price) > 0 && Number(props.stock.high_price) > 0))

function setQuantity(quantity: number) {
  emit('update:quantity', Math.max(100, Math.floor(quantity / 100) * 100))
}

function chooseOrderType(type: 'MARKET' | 'LIMIT') {
  emit('update:orderType', type)
  if (type === 'LIMIT' && props.stock) emit('update:limitPrice', Number(props.stock.price))
}
</script>

<template>
  <aside class="panel order-panel">
    <template v-if="stock">
      <div class="ticket-label"><span>ORDER TICKET</span><strong>{{ orderType === 'MARKET' ? '市价委托' : '限价委托' }}</strong></div>
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

      <div class="side-switch">
        <button :class="{ active: side === 'BUY' }" @click="emit('update:side', 'BUY')">买入</button>
        <button :class="{ active: side === 'SELL' }" @click="emit('update:side', 'SELL')">卖出</button>
      </div>
      <div class="form-group">
        <label>委托方式 <small>A 股规则模拟</small></label>
        <div class="order-type-switch">
          <button :class="{ active: orderType === 'MARKET' }" @click="chooseOrderType('MARKET')">市价</button>
          <button :class="{ active: orderType === 'LIMIT' }" @click="chooseOrderType('LIMIT')">限价</button>
        </div>
      </div>
      <div v-if="orderType === 'LIMIT'" class="form-group">
        <label>委托价格 <small v-if="preview">范围 {{ preview.lower_limit }}—{{ preview.upper_limit }}</small></label>
        <div class="stepper price-stepper">
          <button @click="emit('update:limitPrice', Math.max(0.01, limitPrice - 0.01))">−</button>
          <input
            :value="limitPrice"
            type="number"
            min="0.01"
            step="0.01"
            @input="emit('update:limitPrice', Number(($event.target as HTMLInputElement).value))"
          />
          <button @click="emit('update:limitPrice', limitPrice + 0.01)">＋</button>
        </div>
      </div>
      <div class="form-group">
        <label>委托数量 <small>100股/手 · 最大 {{ preview?.max_quantity ?? 0 }} 股</small></label>
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
          <button v-for="value in [100, 500, 1000, 2000]" :key="value" @click="setQuantity(value)">{{ value }}</button>
        </div>
      </div>

      <div class="order-review risk-review" :class="{ blocked: preview && !preview.allowed }">
        <span>预估金额<strong>¥ {{ formatNumber(estimatedAmount) }}</strong></span>
        <span>预估费用<strong>¥ {{ formatNumber(preview?.estimated_fee ?? 0) }}</strong></span>
        <span v-if="side === 'BUY'">成交后现金<strong>¥ {{ formatNumber(preview?.post_available_cash ?? account?.available_cash ?? 0) }}</strong></span>
        <span v-else>T+1 可卖<strong>{{ preview?.sellable_quantity ?? position?.sellable_quantity ?? 0 }} 股</strong></span>
        <span>成交后仓位<strong>{{ formatNumber(preview?.post_position_ratio ?? 0) }}%</strong></span>
      </div>
      <div v-if="preview?.blocking_reason" class="ticket-blocker"><b>!</b>{{ preview.blocking_reason }}</div>
      <div v-else-if="preview?.warnings.length" class="ticket-warnings">
        <span v-for="warning in preview.warnings" :key="warning">△ {{ warning }}</span>
      </div>
      <button
        class="submit-order"
        :class="side === 'BUY' ? 'buy-button' : 'sell-button'"
        :disabled="submitting || previewLoading || !preview?.allowed"
        @click="emit('submit')"
      >
        {{ submitting ? '提交中…' : previewLoading ? '校验中…' : `复核并${side === 'BUY' ? '买入' : '卖出'} ${stock.name}` }}
      </button>
      <p class="trade-note">市价单即时成交；限价单未触价时进入待成交。买入股票当日不可卖出。</p>
    </template>
  </aside>
</template>
