<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import MarketMatrix from './components/MarketMatrix.vue'
import OrderTicket from './components/OrderTicket.vue'
import TradingRecords from './components/TradingRecords.vue'
import { useMarketData } from './composables/useMarketData'
import { useTrading } from './composables/useTrading'
import type { Stock } from './types'
import {
  compactTurnover,
  formatNumber,
  fullDateTime,
  relativeTime,
  riseClass,
  shortDateTime,
  utcDate,
} from './utils/formatters'

type TradingTab = 'positions' | 'orders' | 'trades'

interface OrderNotice {
  kind: 'success' | 'error'
  title: string
  message: string
  meta: string
}

const {
  stocks,
  marketStatus,
  marketIndices,
  loading: marketLoading,
  error: marketError,
  startMarketPolling,
  stopMarketPolling,
} = useMarketData()

const {
  account,
  positions,
  orders,
  trades,
  submitting,
  error: tradingError,
  initializeTrading,
  refreshHistory,
  placeOrder,
  clearError: clearTradingError,
} = useTrading()

const selectedSymbol = ref('600519')
const side = ref<'BUY' | 'SELL'>('BUY')
const quantity = ref(100)
const activeTab = ref<TradingTab>('positions')
const orderNotice = ref<OrderNotice | null>(null)
const currentTime = ref(Date.now())
let clockTimer: number | undefined
let orderNoticeTimer: number | undefined

const error = computed(() => marketError.value || tradingError.value)
const selectedStock = computed(() =>
  stocks.value.find((stock) => stock.symbol === selectedSymbol.value),
)
const selectedPosition = computed(() =>
  positions.value.find((position) => position.symbol === selectedSymbol.value),
)

const positionRatio = computed(() => {
  if (!account.value) return 0
  const totalAssets = Number(account.value.total_assets)
  if (!totalAssets) return 0
  return Math.min(100, Math.max(0, Number(account.value.market_value) / totalAssets * 100))
})

const marketTurnover = computed(() =>
  marketIndices.value
    .filter((item) => item.symbol === '000001' || item.symbol === '399001')
    .reduce((total, item) => total + Number(item.turnover), 0),
)

const marketIndexUpdatedAt = computed(() => {
  const timestamps = marketIndices.value
    .map((item) => utcDate(item.updated_at)?.getTime() ?? 0)
    .filter(Boolean)
  if (!timestamps.length) return null
  return new Date(Math.max(...timestamps)).toISOString()
})

const marketStatusClass = computed(() =>
  marketStatus.value ? `status-${marketStatus.value.status}` : 'status-unavailable',
)

const marketWarning = computed(() => {
  const status = marketStatus.value
  if (!status || !['partial', 'delayed', 'stale', 'source_error', 'unavailable'].includes(status.status)) {
    return ''
  }
  const snapshot = status.latest_quote_at
    ? ` 最近成功行情：${fullDateTime(status.latest_quote_at)}。`
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

function clearError() {
  marketError.value = ''
  clearTradingError()
}

function closeOrderNotice() {
  orderNotice.value = null
  window.clearTimeout(orderNoticeTimer)
}

function showOrderNotice(notice: OrderNotice) {
  orderNotice.value = notice
  window.clearTimeout(orderNoticeTimer)
  orderNoticeTimer = window.setTimeout(() => (orderNotice.value = null), 5200)
}

function selectStock(stock: Stock) {
  selectedSymbol.value = stock.symbol
}

function chooseForSell(symbol: string) {
  selectedSymbol.value = symbol
  side.value = 'SELL'
  window.scrollTo({ top: 180, behavior: 'smooth' })
}

function setActiveTab(tab: TradingTab) {
  activeTab.value = tab
  if (tab !== 'positions') void refreshHistory()
}

async function submitOrder() {
  if (!selectedStock.value || quantity.value <= 0 || quantity.value % 100 !== 0) {
    showOrderNotice({
      kind: 'error',
      title: '委托未提交',
      message: '委托数量必须是 100 股的整数倍',
      meta: selectedStock.value
        ? `${side.value === 'BUY' ? '买入' : '卖出'} ${selectedStock.value.name}`
        : '请选择有效股票',
    })
    return
  }

  const stock = selectedStock.value
  const orderSide = side.value
  const orderQuantity = quantity.value
  try {
    const order = await placeOrder(stock.symbol, orderSide, orderQuantity)
    if (!order) return
    showOrderNotice({
      kind: 'success',
      title: `${orderSide === 'BUY' ? '买入' : '卖出'}已成交`,
      message: `${stock.name} ${orderQuantity.toLocaleString('zh-CN')} 股 · ¥ ${formatNumber(order.price)}`,
      meta: `订单 ${order.order_no}`,
    })
  } catch (reason) {
    showOrderNotice({
      kind: 'error',
      title: `${orderSide === 'BUY' ? '买入' : '卖出'}失败`,
      message: reason instanceof Error ? reason.message : '系统未能完成委托，请稍后重试',
      meta: `${stock.symbol} ${stock.name} · ${orderQuantity.toLocaleString('zh-CN')} 股`,
    })
  }
}

onMounted(async () => {
  await Promise.all([startMarketPolling(), initializeTrading()])
  clockTimer = window.setInterval(() => (currentTime.value = Date.now()), 1000)
})

onBeforeUnmount(() => {
  stopMarketPolling()
  window.clearInterval(clockTimer)
  window.clearTimeout(orderNoticeTimer)
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="logo">MP</div>
        <div>
          <div class="brand-name">MoniPan</div>
          <div class="brand-subtitle">MARKET PRACTICE SYSTEM</div>
        </div>
      </div>
      <div class="desk-context">
        <span class="context-index">01</span>
        <div><strong>交易驾驶舱</strong><small>MARKET OPERATIONS</small></div>
      </div>
      <div class="market-status" :class="marketStatusClass">
        <span class="status-dot"></span>
        <strong>{{ marketStatus?.status_label ?? (marketLoading ? '连接行情服务' : '状态未知') }}</strong>
        <span class="status-note">
          {{ marketStatus ? `${marketStatus.session_label} · 新鲜 ${marketStatus.fresh_count}/${marketStatus.total_count}` : '等待状态数据' }}
        </span>
      </div>
      <div class="user-chip">
        <span class="avatar">演</span>
        <div><strong>演示账户</strong><small>初始资金 ¥1,000,000</small></div>
      </div>
    </header>

    <main>
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
        <div class="focus-quote">
          <span>焦点标的</span>
          <strong>{{ selectedStock.symbol }}</strong>
          <b>{{ selectedStock.name }}</b>
          <em :class="riseClass(selectedStock.change)">{{ formatNumber(selectedStock.price) }}</em>
          <small :class="riseClass(selectedStock.change)">
            {{ Number(selectedStock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(selectedStock.change_percent) }}%
          </small>
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
          <strong>{{ marketStatus ? `${marketStatus.fresh_count} / ${marketStatus.total_count}` : `${stocks.length} / —` }}</strong>
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

      <section v-if="account" class="asset-cockpit">
        <article class="equity-console">
          <div class="console-heading">
            <div><span class="section-kicker">TOTAL EQUITY</span><h2>账户净值</h2></div>
            <span class="console-code">CNY / DEMO-01</span>
          </div>
          <div class="equity-value">¥ {{ formatNumber(account.total_assets) }}</div>
          <div class="equity-footer">
            <span>累计盈亏</span>
            <div :class="riseClass(account.total_profit_loss)">
              {{ Number(account.total_profit_loss) >= 0 ? '+' : '' }}{{ formatNumber(account.total_profit_loss) }}
              <small>{{ Number(account.total_return_percent) >= 0 ? '+' : '' }}{{ formatNumber(account.total_return_percent) }}%</small>
            </div>
          </div>
        </article>

        <article class="capital-console">
          <div class="console-heading">
            <div><span class="section-kicker">CAPITAL MAP</span><h2>资金分布</h2></div>
            <span class="console-code">{{ positions.length }} POSITIONS</span>
          </div>
          <div class="capital-metrics">
            <div>
              <span>可用资金</span>
              <strong>¥ {{ formatNumber(account.available_cash) }}</strong>
              <small>BUYING POWER</small>
            </div>
            <div>
              <span>持仓市值</span>
              <strong>¥ {{ formatNumber(account.market_value) }}</strong>
              <small>MARKET VALUE</small>
            </div>
          </div>
          <div class="allocation-track">
            <i :style="{ width: `${positionRatio}%` }"></i>
            <span :style="{ left: `${positionRatio}%` }"></span>
          </div>
          <div class="allocation-labels"><span>现金 {{ formatNumber(100 - positionRatio) }}%</span><span>持仓 {{ formatNumber(positionRatio) }}%</span></div>
        </article>

        <article class="exposure-console">
          <div class="console-heading">
            <div><span class="section-kicker">EXPOSURE</span><h2>风险敞口</h2></div>
            <span class="risk-state">{{ positionRatio < 70 ? '可控' : '偏高' }}</span>
          </div>
          <div class="exposure-gauge" :style="{ '--exposure': `${positionRatio * 3.6}deg` }">
            <div><strong>{{ formatNumber(positionRatio) }}</strong><span>%</span><small>POSITION</small></div>
          </div>
          <div class="exposure-scale"><span>0</span><i></i><span>100</span></div>
        </article>
      </section>

      <section v-if="account" class="mobile-capital">
        <article>
          <span class="card-label">可用资金</span>
            <strong>¥ {{ formatNumber(account.available_cash) }}</strong>
        </article>
        <article>
          <span class="card-label">持仓市值</span>
            <strong>¥ {{ formatNumber(account.market_value) }}</strong>
        </article>
      </section>

      <div v-if="marketWarning" class="market-data-alert" :class="marketStatusClass">
        <span class="market-alert-icon">!</span>
        <div>
          <strong>{{ marketStatus?.status_label }}</strong>
          <p>{{ marketWarning }}</p>
        </div>
        <span class="market-alert-source">{{ marketStatus?.provider_label }}公开行情</span>
      </div>

      <div v-if="error" class="alert"><span>!</span>{{ error }}<button @click="clearError">×</button></div>
      <Transition name="order-notice">
        <aside
          v-if="orderNotice"
          class="order-notice"
          :class="`order-notice-${orderNotice.kind}`"
          :role="orderNotice.kind === 'error' ? 'alert' : 'status'"
          aria-live="polite"
        >
          <span class="order-notice-mark" aria-hidden="true">
            {{ orderNotice.kind === 'success' ? '✓' : '!' }}
          </span>
          <div class="order-notice-copy">
            <span class="order-notice-kicker">
              {{ orderNotice.kind === 'success' ? 'ORDER FILLED' : 'ORDER REJECTED' }}
            </span>
            <strong>{{ orderNotice.title }}</strong>
            <p>{{ orderNotice.message }}</p>
            <small>{{ orderNotice.meta }}</small>
          </div>
          <button type="button" aria-label="关闭下单提示" @click="closeOrderNotice">×</button>
        </aside>
      </Transition>

      <section class="workspace">
        <MarketMatrix
          :stocks="stocks"
          :selected-symbol="selectedSymbol"
          :total-count="marketStatus?.total_count ?? stocks.length"
          :loading="marketLoading"
          @select="selectStock"
        />
        <OrderTicket
          v-model:side="side"
          v-model:quantity="quantity"
          :stock="selectedStock"
          :market-status="marketStatus"
          :account="account"
          :position="selectedPosition"
          :submitting="submitting"
          :current-time="currentTime"
          @submit="submitOrder"
        />
      </section>

      <TradingRecords
        :positions="positions"
        :orders="orders"
        :trades="trades"
        :active-tab="activeTab"
        @update:active-tab="setActiveTab"
        @choose-for-sell="chooseForSell"
      />
    </main>

    <footer>
      <span>MONIPAN / MARKET PRACTICE SYSTEM</span>
      <span>股票行情来自东方财富公开接口 · 账户与成交均为模拟数据 · 不构成投资建议</span>
      <span>REAL QUOTES · SIMULATED ORDERS</span>
    </footer>
  </div>
</template>
