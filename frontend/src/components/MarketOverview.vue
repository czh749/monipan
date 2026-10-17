<script setup lang="ts">
import { gsap } from 'gsap'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { MarketIndex, MarketStatus, Stock } from '../types'
import { compactTurnover, formatNumber, fullDateTime, relativeTime, riseClass, shortDateTime, utcDate } from '../utils/formatters'

const props = defineProps<{
  selectedStock?: Stock
  marketStatus: MarketStatus | null
  marketIndices: MarketIndex[]
  stockCount: number
  currentTime: number
}>()

const emit = defineEmits<{ showDetails: [stock: Stock] }>()
const focusQuoteRoot = ref<HTMLElement | null>(null)
let focusQuoteMotion: ReturnType<typeof gsap.matchMedia> | null = null

const marketTurnover = computed(() =>
  props.marketIndices
    .filter((item) => item.symbol === '000001' || item.symbol === '399001')
    .reduce((total, item) => total + Number(item.turnover), 0),
)

const marketIndexUpdatedAt = computed(() => {
  const timestamps = props.marketIndices
    .map((item) => utcDate(item.updated_at)?.getTime() ?? 0)
    .filter(Boolean)
  if (!timestamps.length) return null
  return new Date(Math.max(...timestamps)).toISOString()
})

const marketStatusClass = computed(() =>
  props.marketStatus ? `status-${props.marketStatus.status}` : 'status-unavailable',
)

const marketWarning = computed(() => {
  const status = props.marketStatus
  if (!status || !['partial', 'delayed', 'stale', 'source_error', 'unavailable'].includes(status.status)) {
    return ''
  }
  const snapshot = status.latest_quote_at
    ? ` 最近源行情：${fullDateTime(status.latest_quote_at)}。`
    : ''
  return `${status.status_message}${snapshot}`
})

function indexPulseStyle(value: string | number) {
  const parsed = Number(value)
  const width = Math.min(48, Math.max(1.5, Math.abs(parsed) / 3 * 48))
  return parsed >= 0
    ? { left: '50%', width: `${width}%` }
    : { right: '50%', width: `${width}%` }
}

async function animateFocusQuote() {
  await nextTick()
  focusQuoteMotion?.revert()
  focusQuoteMotion = null

  const root = focusQuoteRoot.value
  if (!root || !props.selectedStock) return

  focusQuoteMotion = gsap.matchMedia()
  focusQuoteMotion.add('(prefers-reduced-motion: no-preference)', () => {
    const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
    timeline
      .fromTo(
        'strong, b, em, small',
        { autoAlpha: 0.4, y: 4 },
        { autoAlpha: 1, y: 0, duration: 0.18, stagger: 0.025, clearProps: 'transform,opacity,visibility' },
      )
      .fromTo(
        '.focus-signal-track i',
        { scaleX: 0, transformOrigin: 'left center' },
        { scaleX: 1, duration: 0.24, clearProps: 'transform' },
        0,
      )
  }, root)
}

watch(() => props.selectedStock?.symbol, () => void animateFocusQuote())
onMounted(() => void animateFocusQuote())
onBeforeUnmount(() => focusQuoteMotion?.revert())
</script>

<template>
  <section v-if="selectedStock" class="market-tape" :class="marketStatusClass" aria-label="行情可信度">
    <div class="feed-health">
      <div class="feed-health-main">
        <span class="status-dot"></span>
        <div>
          <span>行情状态</span>
          <strong>{{ marketStatus?.status_label ?? '正在连接' }}</strong>
        </div>
      </div>
      <small>
        {{ marketStatus?.batch_total_count
          ? `批次 ${marketStatus.batch_success_count}/${marketStatus.batch_total_count} · ${marketStatus.batch_fallback_used ? '备用节点' : '主节点'}`
          : '读取批次状态…' }}
      </small>
    </div>
    <div ref="focusQuoteRoot" class="focus-quote">
      <span class="focus-signal-track" aria-hidden="true"><i></i></span>
      <span>焦点标的</span>
      <strong>{{ selectedStock.symbol }}</strong>
      <b>{{ selectedStock.name }}</b>
      <em :class="riseClass(selectedStock.change)">{{ formatNumber(selectedStock.price) }}</em>
      <small :class="riseClass(selectedStock.change)">
        {{ Number(selectedStock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(selectedStock.change_percent) }}%
      </small>
      <button
        type="button"
        class="focus-detail-action"
        :aria-label="`打开${selectedStock.name}研究详情`"
        @click="emit('showDetails', selectedStock)"
      >研究详情 <span aria-hidden="true">↗</span></button>
    </div>
    <div class="tape-item">
      <span>数据来源</span>
      <strong>{{ marketStatus?.provider_label ?? '东方财富' }}</strong>
      <small>真实行情</small>
    </div>
    <div class="tape-item tape-update">
      <span>最近更新</span>
      <strong>{{ shortDateTime(marketStatus?.latest_quote_at) }}</strong>
      <small>{{ relativeTime(marketStatus?.latest_quote_at, currentTime) }}</small>
    </div>
    <div class="tape-item">
      <span>新鲜覆盖</span>
      <strong>{{ marketStatus ? `${marketStatus.fresh_count} / ${marketStatus.total_count}` : `${stockCount} / —` }}</strong>
      <small>{{ marketStatus ? `${marketStatus.fresh_coverage_percent}% · 标准 ${marketStatus.healthy_coverage_threshold_percent}%` : '读取中' }}</small>
    </div>
    <div
      class="tape-disclaimer"
      title="股票价格来自第三方公开行情接口；账户、委托和成交均为本系统模拟数据。"
    >
      真实行情 / 模拟交易
    </div>
  </section>

  <section v-if="marketIndices.length" class="index-board" aria-label="A股大盘数据">
    <div class="index-board-head">
      <div class="index-board-title">
        <span class="section-kicker">MARKET PULSE</span>
        <div><h2>A股大盘</h2><small>五个基准指数的方向、强度与成交活跃度</small></div>
      </div>
      <div class="market-turnover">
        <span>沪深成交额</span>
        <strong>{{ compactTurnover(marketTurnover) }}</strong>
        <small>{{ marketStatus?.session_label ?? 'A股市场' }}</small>
      </div>
      <div class="index-feed-stamp">
        <span>东方财富 · 真实指数</span>
        <time :datetime="marketIndexUpdatedAt ?? undefined">
          {{ shortDateTime(marketIndexUpdatedAt) }} · {{ relativeTime(marketIndexUpdatedAt, currentTime) }}
        </time>
      </div>
    </div>
    <div class="index-rail">
      <article
        v-for="(item, index) in marketIndices"
        :key="`${item.exchange}-${item.symbol}`"
        class="index-node"
        :class="{ primary: index === 0 }"
      >
        <div class="index-node-head">
          <div><strong>{{ item.name }}</strong><small>{{ item.symbol }} · {{ item.exchange }}</small></div>
          <span>{{ String(index + 1).padStart(2, '0') }}</span>
        </div>
        <div class="index-quote">
          <strong>{{ formatNumber(item.price) }}</strong>
          <div :class="riseClass(item.change)">
            <span>{{ Number(item.change) >= 0 ? '+' : '' }}{{ formatNumber(item.change) }}</span>
            <b>{{ Number(item.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(item.change_percent) }}%</b>
          </div>
        </div>
        <div class="index-pulse-track" aria-hidden="true">
          <span></span>
          <i :class="riseClass(item.change_percent)" :style="indexPulseStyle(item.change_percent)"></i>
        </div>
        <div class="index-node-foot">
          <span>成交额</span><strong>{{ compactTurnover(item.turnover) }}</strong>
        </div>
      </article>
    </div>
  </section>

  <div v-if="marketWarning" class="market-data-alert" :class="marketStatusClass">
    <span class="market-alert-icon">!</span>
    <div>
      <strong>{{ marketStatus?.status_label }}</strong>
      <p>{{ marketWarning }}</p>
    </div>
    <span class="market-alert-source">{{ marketStatus?.provider_label }}公开行情</span>
  </div>
</template>
