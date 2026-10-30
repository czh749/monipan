<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import AccountSummary from './components/AccountSummary.vue'
import AuthGateway from './components/AuthGateway.vue'
import MarketOverview from './components/MarketOverview.vue'
import OrderNotice from './components/OrderNotice.vue'
import MarketMatrix from './components/MarketMatrix.vue'
import SecurityExplorer from './components/SecurityExplorer.vue'
import OrderConfirmation from './components/OrderConfirmation.vue'
import OrderTicket from './components/OrderTicket.vue'
import ReviewWorkspace from './components/ReviewWorkspace.vue'
import StockAnalysisWorkspace from './components/StockAnalysisWorkspace.vue'
import StockDetailsDrawer from './components/StockDetailsDrawer.vue'
import TradingRecords from './components/TradingRecords.vue'
import { api } from './api'
import { useMarketData } from './composables/useMarketData'
import { useOrderEntry } from './composables/useOrderEntry'
import { useTrading } from './composables/useTrading'
import { useTradingScene } from './composables/useTradingScene'
import { useWatchlist } from './composables/useWatchlist'
import type { AuthRequest, CurrentUser, Stock } from './types'
type TradingTab = 'positions' | 'orders' | 'trades'
type AppView = 'trading' | 'account' | 'review'

const {
  stocks,
  marketStatus,
  marketIndices,
  loading: marketLoading,
  error: marketError,
  refreshMarket,
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
const activeTab = ref<TradingTab>('positions')
const activeView = ref<AppView>('trading')
const currentTime = ref(Date.now())
const detailOpen = ref(false)
const analysisOpen = ref(false)
const currentUser = ref<CurrentUser | null>(null)
const authChecking = ref(true)
const authSubmitting = ref(false)
const authError = ref('')
let clockTimer: number | undefined

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

const marketStatusClass = computed(() =>
  marketStatus.value ? `status-${marketStatus.value.status}` : 'status-unavailable',
)

const tradingSceneActive = computed(() => Boolean(currentUser.value) && activeView.value === 'trading')
const { appShellRoot, tradingBackdrop, tradingGlow, animateTradingStage } = useTradingScene(tradingSceneActive)
const {
  side, orderType, limitPrice, quantity,
  orderNotice, orderPreview, previewLoading, confirmationOpen,
  closeOrderNotice, refreshOrderPreview, reviewOrder, confirmOrder, handleCancelOrder,
} = useOrderEntry({
  selectedSymbol, selectedStock, currentUser, currentTime, active: tradingSceneActive,
  account, positions, orders, placeOrder, cancelOrder,
})

function clearError() {
  marketError.value = ''
  watchlistError.value = ''
  clearTradingError()
}

function selectStock(stock: Stock) {
  selectedSymbol.value = stock.symbol
}

async function changeResearchWatchlist(symbol: string) {
  const user = currentUser.value
  await toggleWatchlist(symbol)
  if (user && currentUser.value === user) {
    await refreshMarket()
    if (!stocks.value.some((item) => item.symbol === selectedSymbol.value)) {
      selectedSymbol.value = stocks.value[0]?.symbol ?? '600519'
      detailOpen.value = false
      analysisOpen.value = false
    }
  }
}

function openResearchSymbol(symbol: string) {
  const stock = stocks.value.find((item) => item.symbol === symbol)
  if (stock) showStockDetails(stock)
}

async function openPersonalCenter() {
  if (!currentUser.value) return
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
    await Promise.all([startMarketPolling(), initializePrivateWorkspace()])
    await refreshOrderPreview()
    await animateTradingStage()
  } catch (reason) {
    authError.value = reason instanceof Error ? reason.message : '认证失败，请稍后重试'
  } finally {
    authSubmitting.value = false
  }
}

async function handleLogout() {
  stopMarketPolling()
  try {
    await api.logout()
  } finally {
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

onMounted(async () => {
  try {
    currentUser.value = await api.currentUser()
    await Promise.all([startMarketPolling(), initializePrivateWorkspace()])
    await refreshOrderPreview()
  } catch {
    currentUser.value = null
  } finally {
    authChecking.value = false
  }
  await animateTradingStage()
  clockTimer = window.setInterval(() => (currentTime.value = Date.now()), 1000)
})

onBeforeUnmount(() => {
  stopMarketPolling()
  window.clearInterval(clockTimer)
})
</script>

<template>
  <div
    ref="appShellRoot"
    class="app-shell"
    :class="{ 'trading-scene-active': tradingSceneActive }"
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
        <strong>{{ !currentUser ? '请登录' : marketStatus?.status_label ?? (marketLoading ? '连接行情服务' : '状态未知') }}</strong>
        <span class="status-note">
          {{ !currentUser ? '登录后读取个人行情' : marketStatus ? `${marketStatus.session_label} · 新鲜 ${marketStatus.fresh_count}/${marketStatus.total_count}` : '等待状态数据' }}
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

      <MarketOverview
        v-if="activeView === 'trading'"
        :selected-stock="selectedStock"
        :market-status="marketStatus"
        :market-indices="marketIndices"
        :stock-count="stocks.length"
        :current-time="currentTime"
        @show-details="showStockDetails"
      />

      <AccountSummary
        v-if="activeView === 'account' && account"
        :account="account"
        :username="currentUser.username"
        :position-count="positions.length"
        :position-ratio="positionRatio"
      />

      <div v-if="error" class="alert"><span>!</span>{{ error }}<button @click="clearError">×</button></div>
      <OrderNotice :notice="orderNotice" @close="closeOrderNotice" />

      <SecurityExplorer v-if="activeView === 'trading' && currentUser" :key="currentUser.username" :watchlist-symbols="watchlistSymbols" :change-watchlist="changeResearchWatchlist" @open="openResearchSymbol" />
      <section v-if="activeView === 'trading'" class="workspace">
        <MarketMatrix
          :stocks="stocks"
          :selected-symbol="selectedSymbol"
          :total-count="stocks.length"
          :loading="marketLoading"
          :watchlist-symbols="watchlistSymbols"
          :position-symbols="positionSymbols"
          @select="selectStock"
          @toggle-watchlist="(symbol: string) => changeResearchWatchlist(symbol).catch(() => {})"
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
