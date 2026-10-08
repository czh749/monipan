<script setup lang="ts">
import { gsap } from 'gsap'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AuthGateway from './components/AuthGateway.vue'
import MarketMatrix from './components/MarketMatrix.vue'
import OrderConfirmation from './components/OrderConfirmation.vue'
import OrderTicket from './components/OrderTicket.vue'
import ReviewWorkspace from './components/ReviewWorkspace.vue'
import StockAnalysisWorkspace from './components/StockAnalysisWorkspace.vue'
import StockDetailsDrawer from './components/StockDetailsDrawer.vue'
import TradingRecords from './components/TradingRecords.vue'
import { api } from './api'
import { useMarketData } from './composables/useMarketData'
import { useTrading } from './composables/useTrading'
import { useWatchlist } from './composables/useWatchlist'
import type { AuthRequest, CurrentUser, OrderPreview, Stock } from './types'
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
type AppView = 'trading' | 'account' | 'review'

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
  refreshPortfolio,
  refreshHistory,
  placeOrder,
  cancelOrder,
  clearError: clearTradingError,
  resetTrading,
} = useTrading()

const {
  symbols: watchlistSymbols,
  error: watchlistError,
  loadWatchlist,
  toggleWatchlist,
  resetWatchlist,
} = useWatchlist()

const selectedSymbol = ref('600519')
const side = ref<'BUY' | 'SELL'>('BUY')
const orderType = ref<'MARKET' | 'LIMIT'>('MARKET')
const limitPrice = ref(0)
const quantity = ref(100)
const activeTab = ref<TradingTab>('positions')
const activeView = ref<AppView>('trading')
const orderNotice = ref<OrderNotice | null>(null)
const currentTime = ref(Date.now())
const orderPreview = ref<OrderPreview | null>(null)
const previewLoading = ref(false)
const confirmationOpen = ref(false)
const detailOpen = ref(false)
const analysisOpen = ref(false)
const currentUser = ref<CurrentUser | null>(null)
const authChecking = ref(true)
const authSubmitting = ref(false)
const authError = ref('')
const appShellRoot = ref<HTMLElement | null>(null)
const focusQuoteRoot = ref<HTMLElement | null>(null)
const tradingBackdrop = ref<HTMLElement | null>(null)
const tradingGlow = ref<HTMLElement | null>(null)
let clockTimer: number | undefined
let orderNoticeTimer: number | undefined
let previewTimer: number | undefined
let previewRequestId = 0
let focusQuoteMotion: ReturnType<typeof gsap.matchMedia> | null = null
let tradingStageMotion: ReturnType<typeof gsap.matchMedia> | null = null

const error = computed(() => marketError.value || tradingError.value || watchlistError.value)
const selectedStock = computed(() =>
  stocks.value.find((stock) => stock.symbol === selectedSymbol.value),
)
const selectedPosition = computed(() =>
  positions.value.find((position) => position.symbol === selectedSymbol.value),
)
const positionSymbols = computed(() => positions.value.map((position) => position.symbol))

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

function clearError() {
  marketError.value = ''
  watchlistError.value = ''
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

async function openPersonalCenter() {
  if (!currentUser.value) return
  tradingStageMotion?.revert()
  tradingStageMotion = null
  activeView.value = 'account'
  detailOpen.value = false
  analysisOpen.value = false
  confirmationOpen.value = false
  await Promise.all([refreshPortfolio(), refreshHistory()])
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function openTradingDesk() {
  activeView.value = 'trading'
  await animateTradingStage()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function openReview() {
  activeView.value = 'review'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showStockDetails(stock: Stock) {
  selectedSymbol.value = stock.symbol
  detailOpen.value = true
}

function prepareOrderFromDrawer(orderSide: 'BUY' | 'SELL') {
  side.value = orderSide
  detailOpen.value = false
  window.scrollTo({ top: 180, behavior: 'smooth' })
}

function openStockAnalysis() {
  detailOpen.value = false
  analysisOpen.value = true
}

function closeStockAnalysis() {
  analysisOpen.value = false
  detailOpen.value = true
}

function chooseForSell(symbol: string) {
  selectedSymbol.value = symbol
  side.value = 'SELL'
  activeView.value = 'trading'
  nextTick(() => {
    void animateTradingStage()
    window.scrollTo({ top: 180, behavior: 'smooth' })
  })
}

function setActiveTab(tab: TradingTab) {
  activeTab.value = tab
  if (tab !== 'positions') void refreshHistory()
}

async function refreshOrderPreview() {
  const stock = selectedStock.value
  if (!currentUser.value || !stock || quantity.value <= 0 || quantity.value % 100 !== 0 || (orderType.value === 'LIMIT' && limitPrice.value <= 0)) {
    previewRequestId++
    orderPreview.value = null
    previewLoading.value = false
    return null
  }
  const requestId = ++previewRequestId
  previewLoading.value = true
  try {
    const result = await api.previewOrder({
      symbol: stock.symbol,
      side: side.value,
      quantity: quantity.value,
      order_type: orderType.value,
      ...(orderType.value === 'LIMIT' ? { limit_price: limitPrice.value } : {}),
    })
    if (requestId === previewRequestId) {
      orderPreview.value = result
      return result
    }
    return null
  } catch (reason) {
    if (requestId === previewRequestId) {
      orderPreview.value = null
      showOrderNotice({
        kind: 'error',
        title: '委托校验失败',
        message: reason instanceof Error ? reason.message : '无法取得下单预览',
        meta: stock.symbol,
      })
    }
    return null
  } finally {
    if (requestId === previewRequestId) previewLoading.value = false
  }
}

function previewMatchesSelection(preview: OrderPreview) {
  return preview.symbol === selectedSymbol.value
    && preview.side === side.value
    && preview.quantity === quantity.value
    && preview.order_type === orderType.value
    && (orderType.value !== 'LIMIT'
      || Number(preview.limit_price).toFixed(2) === Number(limitPrice.value).toFixed(2))
}

async function reviewOrder() {
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

  const latestPreview = await refreshOrderPreview()
  if (!latestPreview?.allowed || !previewMatchesSelection(latestPreview)) {
    showOrderNotice({
      kind: 'error',
      title: '委托未通过校验',
      message: latestPreview?.blocking_reason ?? '请等待风险预览完成',
      meta: selectedStock.value.symbol,
    })
    return
  }
  confirmationOpen.value = true
}

async function confirmOrder() {
  if (!selectedStock.value || !orderPreview.value) return
  if (!orderPreview.value.allowed || !previewMatchesSelection(orderPreview.value)) {
    confirmationOpen.value = false
    await refreshOrderPreview()
    showOrderNotice({
      kind: 'error',
      title: '请重新复核委托',
      message: '行情或委托条件已变化，请确认最新预览后再提交',
      meta: selectedSymbol.value,
    })
    return
  }
  const stock = selectedStock.value
  const orderSide = side.value
  const orderQuantity = quantity.value
  try {
    const order = await placeOrder({
      symbol: stock.symbol,
      side: orderSide,
      quantity: orderQuantity,
      order_type: orderType.value,
      ...(orderType.value === 'LIMIT' ? { limit_price: limitPrice.value } : {}),
    })
    if (!order) return
    confirmationOpen.value = false
    const pending = order.status === 'PENDING'
    showOrderNotice({
      kind: 'success',
      title: pending ? '限价委托已进入队列' : `${orderSide === 'BUY' ? '买入' : '卖出'}已成交`,
      message: pending
        ? `${stock.name} ${orderQuantity.toLocaleString('zh-CN')} 股 · 限价 ¥ ${formatNumber(order.limit_price ?? order.price)}`
        : `${stock.name} ${orderQuantity.toLocaleString('zh-CN')} 股 · ¥ ${formatNumber(order.price)}`,
      meta: `订单 ${order.order_no}`,
    })
    await refreshOrderPreview()
  } catch (reason) {
    showOrderNotice({
      kind: 'error',
      title: `${orderSide === 'BUY' ? '买入' : '卖出'}失败`,
      message: reason instanceof Error ? reason.message : '系统未能完成委托，请稍后重试',
      meta: `${stock.symbol} ${stock.name} · ${orderQuantity.toLocaleString('zh-CN')} 股`,
    })
  }
}

async function handleCancelOrder(orderNo: string) {
  try {
    await cancelOrder(orderNo)
    showOrderNotice({ kind: 'success', title: '委托已撤销', message: '待成交委托已从撮合队列移除', meta: orderNo })
    await refreshOrderPreview()
  } catch (reason) {
    showOrderNotice({ kind: 'error', title: '撤单失败', message: reason instanceof Error ? reason.message : '无法撤销委托', meta: orderNo })
  }
}

async function initializePrivateWorkspace() {
  await Promise.all([initializeTrading(), loadWatchlist()])
  await refreshOrderPreview()
}

async function handleAuthenticate(mode: 'login' | 'register', payload: AuthRequest) {
  if (authSubmitting.value) return
  authSubmitting.value = true
  authError.value = ''
  try {
    currentUser.value = mode === 'login'
      ? await api.login(payload)
      : await api.register(payload)
    activeView.value = 'trading'
    await initializePrivateWorkspace()
    await animateTradingStage()
  } catch (reason) {
    authError.value = reason instanceof Error ? reason.message : '认证失败，请稍后重试'
  } finally {
    authSubmitting.value = false
  }
}

async function handleLogout() {
  try {
    await api.logout()
  } finally {
    tradingStageMotion?.revert()
    tradingStageMotion = null
    currentUser.value = null
    activeView.value = 'trading'
    authError.value = ''
    resetTrading()
    resetWatchlist()
    confirmationOpen.value = false
    detailOpen.value = false
    analysisOpen.value = false
  }
}

async function animateFocusQuote() {
  await nextTick()
  focusQuoteMotion?.revert()
  focusQuoteMotion = null

  const root = focusQuoteRoot.value
  if (!root || !selectedStock.value) return

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

async function animateTradingStage() {
  await nextTick()
  tradingStageMotion?.revert()
  tradingStageMotion = null

  const root = appShellRoot.value
  const backdrop = tradingBackdrop.value
  const glow = tradingGlow.value
  if (!root || !backdrop || !glow || !currentUser.value || activeView.value !== 'trading') return

  tradingStageMotion = gsap.matchMedia()
  tradingStageMotion.add(
    {
      desktop: '(min-width: 921px)',
      compact: '(max-width: 920px)',
      reduceMotion: '(prefers-reduced-motion: reduce)',
    },
    (context) => {
      const { desktop, reduceMotion } = context.conditions as { desktop: boolean; reduceMotion: boolean }
      const panels = root.querySelectorAll<HTMLElement>('.market-tape, .index-board-head, .index-node, .workspace > *')
      const beams = root.querySelectorAll<HTMLElement>('.cockpit-beam')

      if (reduceMotion) {
        gsap.set([backdrop, ...panels, ...beams], { autoAlpha: 1, clearProps: 'transform' })
        return
      }

      const entrance = gsap.timeline({ defaults: { ease: 'power3.out' } })
      entrance
        .fromTo(backdrop, { autoAlpha: .35, scale: 1.075 }, { autoAlpha: 1, scale: 1.025, duration: 1.45 }, 0)
        .fromTo(beams, { autoAlpha: 0, scaleX: 0, transformOrigin: 'left center' }, { autoAlpha: .7, scaleX: 1, duration: .68, stagger: .08 }, .12)
        .fromTo('.market-tape', { autoAlpha: 0, y: -14 }, { autoAlpha: 1, y: 0, duration: .44, clearProps: 'transform,opacity,visibility' }, .18)
        .fromTo('.index-board-head', { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: .48, clearProps: 'transform,opacity,visibility' }, .29)
        .fromTo('.index-node', { autoAlpha: 0, y: 22, scale: .985 }, { autoAlpha: 1, y: 0, scale: 1, duration: .48, stagger: .055, clearProps: 'transform,opacity,visibility' }, .38)
        .fromTo('.workspace > *', { autoAlpha: 0, y: 26 }, { autoAlpha: 1, y: 0, duration: .58, stagger: .1, clearProps: 'transform,opacity,visibility' }, .54)

      if (!desktop) return
      const backdropX = gsap.quickTo(backdrop, 'x', { duration: 1.15, ease: 'power3.out' })
      const backdropY = gsap.quickTo(backdrop, 'y', { duration: 1.15, ease: 'power3.out' })
      const glowX = gsap.quickTo(glow, 'x', { duration: .72, ease: 'power3.out' })
      const glowY = gsap.quickTo(glow, 'y', { duration: .72, ease: 'power3.out' })

      const moveStage = (event: PointerEvent) => {
        const xRatio = event.clientX / Math.max(window.innerWidth, 1) - .5
        const yRatio = event.clientY / Math.max(window.innerHeight, 1) - .5
        backdropX(xRatio * -16)
        backdropY(yRatio * -10)
        glowX(event.clientX - window.innerWidth * .5)
        glowY(event.clientY - window.innerHeight * .5)
      }

      window.addEventListener('pointermove', moveStage, { passive: true })
      return () => window.removeEventListener('pointermove', moveStage)
    },
    root,
  )
}

function enterOrderNotice(element: Element, done: () => void) {
  const target = element as HTMLElement
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const parts = target.querySelectorAll<HTMLElement>('.order-notice-mark, .order-notice-copy, button')
  const signal = target.querySelector<HTMLElement>('.order-notice-scan')
  gsap.killTweensOf([target, ...parts, signal].filter(Boolean))

  if (reducedMotion) {
    gsap.set(target, { autoAlpha: 1 })
    done()
    return
  }

  gsap.timeline({
    defaults: { ease: 'power2.out' },
    onComplete: () => {
      gsap.set(target, { clearProps: 'transform,opacity,visibility' })
      done()
    },
  })
    .fromTo(target, { autoAlpha: 0, y: -10 }, { autoAlpha: 1, y: 0, duration: 0.18 })
    .fromTo(parts, { autoAlpha: 0, x: 6 }, { autoAlpha: 1, x: 0, duration: 0.16, stagger: 0.025 }, 0.045)
    .fromTo(signal, { scaleX: 0, transformOrigin: 'left center' }, { scaleX: 1, duration: 0.28 }, 0)
}

function leaveOrderNotice(element: Element, done: () => void) {
  const target = element as HTMLElement
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  gsap.killTweensOf(target)
  gsap.to(target, {
    autoAlpha: 0,
    y: reducedMotion ? 0 : -6,
    duration: reducedMotion ? 0 : 0.14,
    ease: 'power1.in',
    onComplete: done,
  })
}

watch(selectedStock, (stock, previous) => {
  if (stock && stock.symbol !== previous?.symbol) {
    limitPrice.value = Number(stock.price)
    void animateFocusQuote()
  }
}, { immediate: true })

watch([selectedStock, selectedSymbol, side, quantity, orderType, limitPrice, account, positions, orders], () => {
  window.clearTimeout(previewTimer)
  previewTimer = window.setTimeout(() => void refreshOrderPreview(), 180)
})

watch(() => Math.floor(currentTime.value / 30_000), () => {
  if (currentUser.value && activeView.value === 'trading' && document.visibilityState === 'visible') {
    void refreshOrderPreview()
  }
})

onMounted(async () => {
  const marketReady = startMarketPolling()
  try {
    currentUser.value = await api.currentUser()
    await initializePrivateWorkspace()
  } catch {
    currentUser.value = null
  } finally {
    authChecking.value = false
  }
  await marketReady
  await animateTradingStage()
  clockTimer = window.setInterval(() => (currentTime.value = Date.now()), 1000)
})

onBeforeUnmount(() => {
  stopMarketPolling()
  window.clearInterval(clockTimer)
  window.clearTimeout(orderNoticeTimer)
  window.clearTimeout(previewTimer)
  focusQuoteMotion?.revert()
  tradingStageMotion?.revert()
})
</script>

<template>
  <div
    ref="appShellRoot"
    class="app-shell"
    :class="{ 'trading-scene-active': currentUser && activeView === 'trading' }"
  >
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
        <div>
          <strong>{{ activeView === 'review' ? '学习复盘' : activeView === 'account' && currentUser ? '个人中心' : '交易驾驶舱' }}</strong>
          <small>{{ activeView === 'review' ? 'DAILY REVIEW' : activeView === 'account' && currentUser ? 'ACCOUNT LEDGER' : 'MARKET OPERATIONS' }}</small>
        </div>
      </div>
      <div class="market-status" :class="marketStatusClass">
        <span class="status-dot"></span>
        <strong>{{ marketStatus?.status_label ?? (marketLoading ? '连接行情服务' : '状态未知') }}</strong>
        <span class="status-note">
          {{ marketStatus ? `${marketStatus.session_label} · 新鲜 ${marketStatus.fresh_count}/${marketStatus.total_count}` : '等待状态数据' }}
        </span>
      </div>
      <div class="user-chip" :class="{ anonymous: !currentUser }">
        <button
          v-if="currentUser"
          type="button"
          class="user-center-button"
          :class="{ active: activeView === 'account' || activeView === 'review' }"
          :aria-current="activeView === 'account' || activeView === 'review' ? 'page' : undefined"
          @click="openPersonalCenter"
        >
          <span class="avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span>
          <span class="user-center-copy">
            <strong>个人中心</strong>
            <small>{{ currentUser.username }} · 持仓与记录</small>
          </span>
        </button>
        <template v-else>
          <span class="avatar">访</span>
          <span class="user-center-copy">
            <strong>访客模式</strong>
            <small>请登录交易席位</small>
          </span>
        </template>
        <button v-if="currentUser" class="logout-button" type="button" @click="handleLogout">退出</button>
      </div>
    </header>

    <div v-if="currentUser && activeView === 'trading'" class="trading-atmosphere" aria-hidden="true">
      <div ref="tradingBackdrop" class="trading-atmosphere-image"></div>
      <div class="trading-atmosphere-grid"></div>
      <div ref="tradingGlow" class="trading-atmosphere-glow"></div>
      <div class="trading-atmosphere-vignette"></div>
      <i class="cockpit-beam cockpit-beam-a"></i>
      <i class="cockpit-beam cockpit-beam-b"></i>
      <i class="cockpit-beam cockpit-beam-c"></i>
    </div>

    <main v-if="authChecking" class="auth-loading-shell" aria-live="polite">
      <span class="auth-loading-mark">MP</span>
      <strong>正在核验交易席位…</strong>
    </main>

    <main v-else-if="!currentUser" class="auth-main">
      <AuthGateway
        :loading="authSubmitting"
        :error="authError"
        @authenticate="handleAuthenticate"
      />
    </main>

    <main v-else :class="{ 'account-center-main': activeView === 'account' || activeView === 'review' }">
      <section v-if="activeView === 'account' || activeView === 'review'" class="account-center-head">
        <div class="account-center-identity">
          <span class="account-avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span>
          <div>
            <span class="section-kicker">PERSONAL ACCOUNT CENTER</span>
            <h1>{{ activeView === 'review' ? `${currentUser.username} 的学习复盘` : `${currentUser.username} 的账户账簿` }}</h1>
            <p>{{ activeView === 'review' ? '从每日资产变化回看现金、持仓与每笔成交。' : '集中查看资金分布、当前持仓与全部模拟交易流水。' }}</p>
          </div>
        </div>
        <div class="review-nav-actions">
          <button v-if="activeView === 'account'" type="button" class="account-return" @click="openReview">查看每日复盘 →</button>
          <button v-else type="button" class="account-return" @click="openPersonalCenter">← 返回账户账簿</button>
          <button type="button" class="account-return" @click="openTradingDesk">返回交易驾驶舱</button>
        </div>
        <div class="account-signal-track" aria-hidden="true">
          <span>CAPITAL</span>
          <i><b :style="{ width: `${positionRatio}%` }"></b></i>
          <span>LEDGER</span>
        </div>
      </section>

      <section v-if="activeView === 'trading' && selectedStock" class="market-tape" :class="marketStatusClass" aria-label="行情可信度">
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
            @click="showStockDetails(selectedStock)"
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

      <section v-if="activeView === 'trading' && marketIndices.length" class="index-board" aria-label="A股大盘数据">
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

      <section v-if="activeView === 'account' && account" class="asset-cockpit account-asset-cockpit">
        <article class="equity-console">
          <div class="console-heading">
            <div><span class="section-kicker">TOTAL EQUITY</span><h2>账户净值</h2></div>
            <span class="console-code">CNY / {{ currentUser.username.toUpperCase() }}</span>
          </div>
          <div class="equity-value">¥ {{ formatNumber(account.total_assets) }}</div>
          <div class="equity-footer">
            <span>累计盈亏</span>
            <div :class="riseClass(account.total_profit_loss)">
              {{ Number(account.total_profit_loss) >= 0 ? '+' : '' }}{{ formatNumber(account.total_profit_loss) }}
              <small>{{ Number(account.total_return_percent) >= 0 ? '+' : '' }}{{ formatNumber(account.total_return_percent) }}%</small>
            </div>
          </div>
          <div class="equity-pnl-split">
            <span>已实现 <strong :class="riseClass(account.realized_profit_loss)">{{ formatNumber(account.realized_profit_loss) }}</strong></span>
            <span>浮动 <strong :class="riseClass(account.floating_profit_loss)">{{ formatNumber(account.floating_profit_loss) }}</strong></span>
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

      <section v-if="activeView === 'account' && account" class="mobile-capital">
        <article>
          <span class="card-label">可用资金</span>
            <strong>¥ {{ formatNumber(account.available_cash) }}</strong>
        </article>
        <article>
          <span class="card-label">持仓市值</span>
            <strong>¥ {{ formatNumber(account.market_value) }}</strong>
        </article>
      </section>

      <div v-if="activeView === 'trading' && marketWarning" class="market-data-alert" :class="marketStatusClass">
        <span class="market-alert-icon">!</span>
        <div>
          <strong>{{ marketStatus?.status_label }}</strong>
          <p>{{ marketWarning }}</p>
        </div>
        <span class="market-alert-source">{{ marketStatus?.provider_label }}公开行情</span>
      </div>

      <div v-if="error" class="alert"><span>!</span>{{ error }}<button @click="clearError">×</button></div>
      <Transition :css="false" @enter="enterOrderNotice" @leave="leaveOrderNotice">
        <aside
          v-if="orderNotice"
          class="order-notice"
          :class="`order-notice-${orderNotice.kind}`"
          :role="orderNotice.kind === 'error' ? 'alert' : 'status'"
          aria-live="polite"
        >
          <span class="order-notice-scan" aria-hidden="true"></span>
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

      <section v-if="activeView === 'trading'" class="workspace">
        <MarketMatrix
          :stocks="stocks"
          :selected-symbol="selectedSymbol"
          :total-count="marketStatus?.total_count ?? stocks.length"
          :loading="marketLoading"
          :watchlist-symbols="watchlistSymbols"
          :position-symbols="positionSymbols"
          @select="selectStock"
          @toggle-watchlist="toggleWatchlist"
          @show-details="showStockDetails"
        />
        <OrderTicket
          v-model:side="side"
          v-model:order-type="orderType"
          v-model:limit-price="limitPrice"
          v-model:quantity="quantity"
          :stock="selectedStock"
          :market-status="marketStatus"
          :account="account"
          :position="selectedPosition"
          :preview="orderPreview"
          :preview-loading="previewLoading"
          :submitting="submitting"
          :current-time="currentTime"
          @submit="reviewOrder"
        />
      </section>

      <TradingRecords
        v-if="activeView === 'account'"
        :positions="positions"
        :orders="orders"
        :trades="trades"
        :active-tab="activeTab"
        @update:active-tab="setActiveTab"
        @choose-for-sell="chooseForSell"
        @cancel-order="handleCancelOrder"
      />
      <ReviewWorkspace v-if="activeView === 'review'" />
    </main>

    <StockDetailsDrawer
      :open="detailOpen"
      :stock="selectedStock"
      :position="selectedPosition"
      @close="detailOpen = false"
      @prepare-order="prepareOrderFromDrawer"
      @open-analysis="openStockAnalysis"
    />

    <StockAnalysisWorkspace
      :open="analysisOpen"
      :stock="selectedStock"
      :position="selectedPosition"
      @close="closeStockAnalysis"
    />

    <OrderConfirmation
      v-if="confirmationOpen && selectedStock && orderPreview"
      :stock="selectedStock"
      :preview="orderPreview"
      :submitting="submitting"
      @close="confirmationOpen = false"
      @confirm="confirmOrder"
    />

    <footer>
      <span>MONIPAN / MARKET PRACTICE SYSTEM</span>
      <span>
        股票行情来自东方财富公开接口 · 账户与成交均为模拟数据 · 不构成投资建议 ·
        <a
          class="site-record-link"
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noopener noreferrer"
        >苏ICP备2026059082号-1</a>
      </span>
      <span>REAL QUOTES · SIMULATED ORDERS</span>
    </footer>
  </div>
</template>
