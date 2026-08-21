<script setup lang="ts">
import { gsap } from 'gsap'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import type {
  CompanyAnnouncement,
  FinancialReport,
  PerformanceEvent,
  Position,
  RegulatoryLetter,
  Stock,
  StockAnnouncements,
  StockBar,
  StockFundamentals,
  StockHistory,
  StockRegulatoryLetters,
} from '../types'
import { compactVolume, formatNumber, riseClass } from '../utils/formatters'

const props = defineProps<{
  open: boolean
  stock?: Stock
  position?: Position
}>()

const emit = defineEmits<{
  close: []
  prepareOrder: [side: 'BUY' | 'SELL']
  openAnalysis: []
}>()
type DrawerTab = 'market' | 'fundamentals' | 'events' | 'announcements'
type AnnouncementRange = 30 | 90 | 365

const history = ref<StockHistory | null>(null)
const loading = ref(false)
const error = ref('')
const hoveredIndex = ref<number | null>(null)
const activeTab = ref<DrawerTab>('market')
const fundamentals = ref<StockFundamentals | null>(null)
const fundamentalsLoading = ref(false)
const fundamentalsError = ref('')
const trendMetric = ref<'revenue' | 'net_profit_parent'>('revenue')
const announcements = ref<StockAnnouncements | null>(null)
const regulatoryLetters = ref<StockRegulatoryLetters | null>(null)
const announcementsLoading = ref(false)
const announcementsError = ref('')
const regulatoryError = ref('')
const announcementRange = ref<AnnouncementRange>(90)
const announcementLimit = ref(20)
const drawerRoot = ref<HTMLElement | null>(null)
const announcementRanges: { value: AnnouncementRange; label: string }[] = [
  { value: 30, label: '30天' },
  { value: 90, label: '90天' },
  { value: 365, label: '1年' },
]
let fundamentalsRequestId = 0
let announcementsRequestId = 0
let drawerMotion: ReturnType<typeof gsap.matchMedia> | null = null

interface RenderedStockBar extends StockBar {
  x: number
  wickTop: number
  wickBottom: number
  bodyTop: number
  bodyHeight: number
  closeY: number
  candleWidth: number
  volumeY: number
  volumeHeight: number
  dayDirection: 'rise' | 'fall' | 'flat'
  candleDirection: 'positive' | 'negative' | 'flat'
  candleLabel: '阳线' | '阴线' | '十字线'
  previousClose: number | null
  changeAmount: number | null
  changePercent: number | null
  openChangeAmount: number
  openChangePercent: number | null
  amplitudePercent: number | null
  hitX: number
  hitWidth: number
}

interface ChartModel {
  bars: RenderedStockBar[]
  guides: { y: number; label: number }[]
  dates: RenderedStockBar[]
  costY: number | null
  costOutside: 'above' | 'below' | null
}

interface FinancialTrendPoint {
  x: number
  y: number
  value: number
  label: string
}

interface FinancialTrendChart {
  points: FinancialTrendPoint[]
  polyline: string
  guides: { y: number; value: number }[]
}

type CompanyDynamic =
  | {
      kind: 'announcement'
      date: string
      id: string
      announcement: CompanyAnnouncement
    }
  | {
      kind: 'regulatory'
      date: string
      id: string
      letter: RegulatoryLetter
    }

const chart = computed<ChartModel>(() => {
  const allBars = history.value?.bars ?? []
  const visibleStart = Math.max(0, allBars.length - 50)
  const bars = allBars.slice(visibleStart)
  if (!bars.length) {
    return {
      bars: [],
      guides: [],
      dates: [],
      costY: null as number | null,
      costOutside: null as 'above' | 'below' | null,
    }
  }
  const width = 760
  const priceTop = 20
  const priceHeight = 205
  const volumeTop = 258
  const volumeHeight = 58
  const values = bars.flatMap((bar) => [Number(bar.high_price), Number(bar.low_price)])
  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)
  const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.005)
  const min = rawMin - padding
  const max = rawMax + padding
  const range = Math.max(max - min, 0.01)
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1)
  const step = width / bars.length
  const candleWidth = Math.max(3, Math.min(9, step * 0.58))
  const y = (value: number) => priceTop + (max - value) / range * priceHeight
  const rendered = bars.map((bar, index) => {
    const open = Number(bar.open_price)
    const close = Number(bar.close_price)
    const high = Number(bar.high_price)
    const low = Number(bar.low_price)
    const previousBar = allBars[visibleStart + index - 1]
    const previousClose = finiteNumber(bar.prev_close)
      ?? (previousBar ? Number(previousBar.close_price) : null)
    const changeAmount = finiteNumber(bar.change)
      ?? (previousClose === null ? null : close - previousClose)
    const changePercent = finiteNumber(bar.change_percent)
      ?? (previousClose && changeAmount !== null
        ? changeAmount / previousClose * 100
        : null)
    const openChangeAmount = close - open
    const openChangePercent = open > 0 ? openChangeAmount / open * 100 : null
    const amplitudePercent = previousClose && previousClose > 0
      ? (high - low) / previousClose * 100
      : null
    const dayDirection: RenderedStockBar['dayDirection'] = changeAmount === null
      ? (openChangeAmount > 0 ? 'rise' : openChangeAmount < 0 ? 'fall' : 'flat')
      : (changeAmount > 0 ? 'rise' : changeAmount < 0 ? 'fall' : 'flat')
    const candleDirection: RenderedStockBar['candleDirection'] = openChangeAmount > 0
      ? 'positive'
      : openChangeAmount < 0
        ? 'negative'
        : 'flat'
    const candleLabel: RenderedStockBar['candleLabel'] = candleDirection === 'positive'
      ? '阳线'
      : candleDirection === 'negative'
        ? '阴线'
        : '十字线'
    const x = index * step + step / 2
    return {
      ...bar,
      x,
      wickTop: y(high),
      wickBottom: y(low),
      bodyTop: Math.min(y(open), y(close)),
      bodyHeight: Math.max(1.5, Math.abs(y(open) - y(close))),
      closeY: y(close),
      candleWidth,
      volumeY: volumeTop + volumeHeight - bar.volume / maxVolume * volumeHeight,
      volumeHeight: Math.max(1, bar.volume / maxVolume * volumeHeight),
      dayDirection,
      candleDirection,
      candleLabel,
      previousClose,
      changeAmount,
      changePercent,
      openChangeAmount,
      openChangePercent,
      amplitudePercent,
      hitX: index * step,
      hitWidth: step,
    }
  })
  const guides = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    y: priceTop + priceHeight * ratio,
    label: max - range * ratio,
  }))
  const dateStep = Math.max(1, Math.floor(bars.length / 5))
  const dates = rendered.filter((_bar, index) => index % dateStep === 0 || index === bars.length - 1)
  const cost = props.position ? Number(props.position.average_cost) : null
  const validCost = cost !== null && Number.isFinite(cost) ? cost : null
  const costOutside = validCost === null
    ? null
    : validCost > max
      ? 'above'
      : validCost < min
        ? 'below'
        : null
  const costY = validCost === null
    ? null
    : costOutside === 'above'
      ? priceTop
      : costOutside === 'below'
        ? priceTop + priceHeight
        : y(validCost)
  return { bars: rendered, guides, dates, costY, costOutside }
})

const hoveredBar = computed(() => {
  if (hoveredIndex.value === null) return null
  return chart.value.bars[hoveredIndex.value] ?? null
})

const tooltipStyle = computed<Record<string, string>>(() => {
  const style: Record<string, string> = {}
  if (hoveredBar.value) {
    style['--kline-anchor'] = `${(hoveredBar.value.x + 18) / 8.2}%`
  }
  return style
})

const latestReport = computed(() => fundamentals.value?.latest_report ?? null)

const canLoadMoreAnnouncements = computed(() => {
  return Boolean(
    (
      (announcements.value?.announcements.length ?? 0) >= announcementLimit.value
      || (regulatoryLetters.value?.letters.length ?? 0) >= announcementLimit.value
    )
    && announcementLimit.value < 200,
  )
})

const companyDynamics = computed<CompanyDynamic[]>(() => {
  const filings: CompanyDynamic[] = (announcements.value?.announcements ?? []).map(
    (announcement) => ({
      kind: 'announcement',
      date: announcement.announcement_date,
      id: `announcement-${announcement.exchange}-${announcement.external_id}`,
      announcement,
    }),
  )
  const letters: CompanyDynamic[] = (regulatoryLetters.value?.letters ?? []).map(
    (letter) => ({
      kind: 'regulatory',
      date: letter.issued_date,
      id: `regulatory-${letter.exchange}-${letter.external_id}`,
      letter,
    }),
  )
  return [...filings, ...letters].sort((left, right) => {
    const dateOrder = right.date.localeCompare(left.date)
    return dateOrder || right.id.localeCompare(left.id)
  })
})

const companyDynamicsCacheStatus = computed(() => {
  const statuses = [
    announcements.value?.cache_status,
    regulatoryLetters.value?.cache_status,
  ].filter(Boolean)
  if (statuses.includes('STALE')) return 'STALE' as const
  if (statuses.includes('REFRESHED')) return 'REFRESHED' as const
  return 'CACHED' as const
})

const companyDynamicsProvider = computed(() => (
  announcements.value?.provider_label
  ?? regulatoryLetters.value?.provider_label
  ?? '证券交易所'
))

const companyDynamicsFetchedAt = computed(() => {
  const values = [
    announcements.value?.fetched_at,
    regulatoryLetters.value?.fetched_at,
  ].filter((value): value is string => Boolean(value))
  return values.sort().at(-1) ?? ''
})

const companyDynamicsRangeStart = computed(() => (
  announcements.value?.range_start ?? regulatoryLetters.value?.range_start ?? ''
))

const companyDynamicsRangeEnd = computed(() => (
  announcements.value?.range_end ?? regulatoryLetters.value?.range_end ?? ''
))

const companyDynamicsErrors = computed(() => (
  [announcementsError.value, regulatoryError.value].filter(Boolean)
))

const trendReports = computed(() => {
  return [...(fundamentals.value?.reports ?? [])].slice(0, 8).reverse()
})

const financialTrend = computed<FinancialTrendChart>(() => {
  const width = 740
  const height = 196
  const top = 18
  const reports = trendReports.value
  const values = reports
    .map((report) => report[trendMetric.value])
    .filter((value): value is string => value !== null)
    .map(Number)
    .filter(Number.isFinite)
  if (!values.length) return { points: [], polyline: '', guides: [] }

  let min = Math.min(0, ...values)
  let max = Math.max(0, ...values)
  if (min === max) max = min + 1
  const padding = (max - min) * 0.08
  min -= padding
  max += padding
  const range = max - min
  const xStep = reports.length > 1 ? width / (reports.length - 1) : 0
  const y = (value: number) => top + (max - value) / range * height
  const points = reports.flatMap((report, index) => {
    const raw = report[trendMetric.value]
    if (raw === null || !Number.isFinite(Number(raw))) return []
    return [{
      x: reports.length > 1 ? index * xStep : width / 2,
      y: y(Number(raw)),
      value: Number(raw),
      label: shortReportLabel(report),
    }]
  })
  return {
    points,
    polyline: points.map((point) => `${point.x},${point.y}`).join(' '),
    guides: [0, 0.5, 1].map((ratio) => ({
      y: top + height * ratio,
      value: max - range * ratio,
    })),
  }
})

const metricCards = computed(() => {
  const report = latestReport.value
  if (!report) return []
  return [
    {
      key: 'revenue',
      label: '营业总收入',
      value: compactMoney(report.revenue),
      delta: report.revenue_yoy,
      note: '累计披露值',
    },
    {
      key: 'profit',
      label: '归母净利润',
      value: compactMoney(report.net_profit_parent),
      delta: report.net_profit_yoy,
      note: '累计披露值',
    },
    {
      key: 'roe',
      label: '加权净资产收益率',
      value: formatMetric(report.weighted_roe, '%'),
      delta: null,
      note: '盈利效率',
    },
    {
      key: 'margin',
      label: '销售毛利率',
      value: formatMetric(report.gross_margin, '%'),
      delta: null,
      note: '主营盈利空间',
    },
    {
      key: 'eps',
      label: '基本每股收益',
      value: formatMetric(report.basic_eps, ' 元', 4),
      delta: null,
      note: 'EPS',
    },
    {
      key: 'cashflow',
      label: '每股经营现金流',
      value: formatMetric(report.operating_cash_flow_per_share, ' 元', 4),
      delta: null,
      note: '现金含量',
    },
  ]
})

function signedValue(value: number | null, suffix = '') {
  if (value === null) return '—'
  return `${value > 0 ? '+' : ''}${formatNumber(value)}${suffix}`
}

function finiteNumber(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function compactMoney(value: string | number | null) {
  if (value === null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  const absolute = Math.abs(number)
  if (absolute >= 100_000_000) return `${formatNumber(number / 100_000_000)} 亿`
  if (absolute >= 10_000) return `${formatNumber(number / 10_000)} 万`
  return `${formatNumber(number)} 元`
}

function formatMetric(value: string | number | null, suffix = '', precision = 2) {
  if (value === null || !Number.isFinite(Number(value))) return '—'
  return `${formatNumber(value, precision)}${suffix}`
}

function shortReportLabel(report: FinancialReport) {
  const suffix = {
    Q1: 'Q1',
    H1: 'H1',
    Q3: 'Q3',
    ANNUAL: '年报',
    OTHER: '报告',
  }[report.report_type]
  return `${report.report_period.slice(2, 4)} ${suffix}`
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(`${value}T00:00:00`))
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value.endsWith('Z') ? value : `${value}Z`))
}

function growthRange(lower: string | null, upper: string | null) {
  if (lower === null && upper === null) return null
  const left = Number(lower ?? upper)
  const right = Number(upper ?? lower)
  if (!Number.isFinite(left) || !Number.isFinite(right)) return null
  if (left === right) return signedValue(left, '%')
  return `${signedValue(left, '%')} ～ ${signedValue(right, '%')}`
}

function growthRangeClass(lower: string | null, upper: string | null) {
  if (lower === null && upper === null) return 'flat'
  const left = Number(lower ?? upper)
  const right = Number(upper ?? lower)
  if (left >= 0 && right >= 0) return 'rise'
  if (left <= 0 && right <= 0) return 'fall'
  return 'flat'
}

function eventSummary(event: PerformanceEvent) {
  if (event.event_type === 'FORECAST' && event.summary) return event.summary
  const fragments: string[] = []
  if (event.revenue_lower !== null) {
    fragments.push(`营业收入 ${compactMoney(event.revenue_lower)}`)
  }
  if (event.net_profit_lower !== null) {
    fragments.push(`归母净利润 ${compactMoney(event.net_profit_lower)}`)
  }
  return fragments.join('，') || '已披露定期财务报告'
}

function cacheLabel(status: StockFundamentals['cache_status']) {
  return {
    REFRESHED: '本次已刷新',
    CACHED: '缓存已命中',
    STALE: '数据源异常 · 使用最近缓存',
  }[status]
}

function exchangeLabel(exchange: CompanyAnnouncement['exchange']) {
  return exchange === 'SSE' ? '上交所' : '深交所'
}

function announcementTypeLabel(announcement: CompanyAnnouncement) {
  if (announcement.announcement_type && announcement.announcement_type !== '其它') {
    return announcement.announcement_type
  }
  return announcement.announcement_heading
    || announcement.announcement_type
    || '公司公告'
}

function replyRelationLabel(method: 'SOURCE' | 'TITLE_DATE') {
  return method === 'SOURCE' ? '交易所直接关联' : '标题与日期关联'
}

function selectBar(index: number) {
  hoveredIndex.value = index
}

function clearMouseSelection(event: PointerEvent) {
  if (event.pointerType === 'mouse') hoveredIndex.value = null
}

function moveBarSelection(direction: -1 | 1) {
  const lastIndex = chart.value.bars.length - 1
  if (lastIndex < 0) return
  const current = hoveredIndex.value ?? lastIndex
  hoveredIndex.value = Math.max(0, Math.min(lastIndex, current + direction))
}

function selectLatestBar() {
  if (hoveredIndex.value === null && chart.value.bars.length) {
    hoveredIndex.value = chart.value.bars.length - 1
  }
}

async function loadHistory(symbol: string) {
  loading.value = true
  error.value = ''
  try {
    history.value = await api.stockHistory(symbol)
  } catch (reason) {
    history.value = null
    error.value = reason instanceof Error ? reason.message : '无法加载历史行情'
  } finally {
    loading.value = false
  }
}

async function loadFundamentals(symbol: string) {
  const requestId = ++fundamentalsRequestId
  fundamentalsLoading.value = true
  fundamentalsError.value = ''
  try {
    const result = await api.stockFundamentals(symbol)
    if (requestId === fundamentalsRequestId) fundamentals.value = result
  } catch (reason) {
    if (requestId === fundamentalsRequestId) {
      fundamentals.value = null
      fundamentalsError.value = reason instanceof Error
        ? reason.message
        : '无法加载公司财务数据'
    }
  } finally {
    if (requestId === fundamentalsRequestId) fundamentalsLoading.value = false
  }
}

async function loadAnnouncements(symbol: string) {
  const requestId = ++announcementsRequestId
  announcementsLoading.value = true
  announcementsError.value = ''
  regulatoryError.value = ''
  try {
    const result = await api.stockAnnouncements(
      symbol,
      announcementRange.value,
      announcementLimit.value,
    )
    if (requestId === announcementsRequestId) announcements.value = result
  } catch (reason) {
    if (requestId === announcementsRequestId) {
      announcements.value = null
      announcementsError.value = reason instanceof Error
        ? reason.message
        : '无法加载公司公告'
    }
  }

  try {
    const result = await api.stockRegulatoryLetters(
      symbol,
      announcementRange.value,
      announcementLimit.value,
    )
    if (requestId === announcementsRequestId) regulatoryLetters.value = result
  } catch (reason) {
    if (requestId === announcementsRequestId) {
      regulatoryLetters.value = null
      regulatoryError.value = reason instanceof Error
        ? reason.message
        : '无法加载监管函件'
    }
  } finally {
    if (requestId === announcementsRequestId) announcementsLoading.value = false
  }
}

function selectAnnouncementRange(days: AnnouncementRange) {
  if (announcementRange.value === days && announcements.value) return
  announcementRange.value = days
  announcementLimit.value = 20
  const symbol = props.stock?.symbol
  if (symbol) void loadAnnouncements(symbol)
}

function loadMoreAnnouncements() {
  const symbol = props.stock?.symbol
  if (!symbol || announcementsLoading.value) return
  announcementLimit.value = Math.min(200, announcementLimit.value + 20)
  void loadAnnouncements(symbol)
}

function selectTab(tab: DrawerTab) {
  activeTab.value = tab
  const symbol = props.stock?.symbol
  if (
    (tab === 'fundamentals' || tab === 'events')
    && symbol
    && !fundamentals.value
    && !fundamentalsLoading.value
  ) {
    void loadFundamentals(symbol)
  }
  if (
    tab === 'announcements'
    && symbol
    && !announcements.value
    && !announcementsLoading.value
  ) {
    void loadAnnouncements(symbol)
  }
}

async function animateDrawerSequence() {
  await nextTick()
  drawerMotion?.revert()
  drawerMotion = null

  const root = drawerRoot.value
  if (!root) return

  drawerMotion = gsap.matchMedia()
  drawerMotion.add('(prefers-reduced-motion: no-preference)', () => {
    const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
    timeline
      .fromTo(
        '.drawer-header > div:first-child, .drawer-header-actions',
        { autoAlpha: 0, x: 10 },
        { autoAlpha: 1, x: 0, duration: 0.2, stagger: 0.035, clearProps: 'transform,opacity,visibility' },
      )
      .fromTo(
        '.drawer-quote > div, .drawer-quote dl > div',
        { autoAlpha: 0, y: 6 },
        { autoAlpha: 1, y: 0, duration: 0.18, stagger: 0.025, clearProps: 'transform,opacity,visibility' },
        0.035,
      )
      .fromTo(
        '.earnings-pulse, .drawer-tabs, .kline-stage',
        { autoAlpha: 0, y: 7 },
        { autoAlpha: 1, y: 0, duration: 0.2, stagger: 0.04, clearProps: 'transform,opacity,visibility' },
        0.1,
      )
  }, root)
}

watch(
  [() => props.open, () => props.stock?.symbol] as const,
  ([open, symbol], [previousOpen, previousSymbol]) => {
    hoveredIndex.value = null
    if (!open || !symbol) return

    const drawerJustOpened = !previousOpen
    const stockActuallyChanged = symbol !== previousSymbol
    if (!drawerJustOpened && !stockActuallyChanged) return

    activeTab.value = 'market'
    fundamentals.value = null
    fundamentalsError.value = ''
    fundamentalsLoading.value = false
    fundamentalsRequestId += 1
    announcements.value = null
    regulatoryLetters.value = null
    announcementsError.value = ''
    regulatoryError.value = ''
    announcementsLoading.value = false
    announcementRange.value = 90
    announcementLimit.value = 20
    announcementsRequestId += 1
    void loadHistory(symbol)
    void loadFundamentals(symbol)
    void animateDrawerSequence()
  },
)

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  drawerMotion?.revert()
})
</script>

<template>
  <Transition name="drawer">
    <div v-if="open && stock" class="drawer-layer" @click.self="emit('close')">
      <aside ref="drawerRoot" class="stock-drawer" role="dialog" aria-modal="true" :aria-label="`${stock.name}详情`">
        <header class="drawer-header">
          <div>
            <span class="section-kicker">MARKET / FINANCIALS / DISCLOSURES</span>
            <h2>{{ stock.name }} <small>{{ stock.symbol }}</small></h2>
          </div>
          <div class="drawer-header-actions">
            <button
              type="button"
              class="drawer-research-launch"
              aria-label="打开 AI 证据研判"
              @click="emit('openAnalysis')"
            >
              <i class="drawer-research-mark" aria-hidden="true">AI</i>
              <span class="drawer-research-copy">
                <b>证据研判</b>
                <small>只读证据链</small>
              </span>
              <strong aria-hidden="true">→</strong>
            </button>
            <button class="drawer-close" aria-label="关闭股票详情" @click="emit('close')">×</button>
          </div>
        </header>

        <div class="drawer-quote">
          <div :class="riseClass(stock.change)">
            <strong>{{ formatNumber(stock.price) }}</strong>
            <span>{{ Number(stock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(stock.change_percent) }}%</span>
          </div>
          <dl>
            <div><dt>今开</dt><dd>{{ formatNumber(stock.open_price) }}</dd></div>
            <div><dt>最高</dt><dd class="rise">{{ formatNumber(stock.high_price) }}</dd></div>
            <div><dt>最低</dt><dd class="fall">{{ formatNumber(stock.low_price) }}</dd></div>
            <div><dt>成交量</dt><dd>{{ compactVolume(stock.volume) }}</dd></div>
          </dl>
        </div>

        <section class="earnings-pulse" aria-label="最新公司业绩">
          <div class="earnings-pulse-identity">
            <span class="section-kicker">EARNINGS PULSE</span>
            <template v-if="latestReport">
              <strong>{{ latestReport.report_name }}</strong>
              <small>{{ formatDate(latestReport.report_period) }} 报告期</small>
            </template>
            <template v-else>
              <strong>最新业绩</strong>
              <small>{{ fundamentalsLoading ? '正在读取披露数据' : '等待可用报告' }}</small>
            </template>
          </div>

          <div v-if="fundamentalsLoading" class="earnings-pulse-state">
            <span class="loading-dot"></span>
            <div><strong>同步公司报告</strong><small>行情可继续查看，财务数据将在后台载入</small></div>
          </div>
          <div v-else-if="fundamentalsError" class="earnings-pulse-state is-error">
            <div><strong>业绩暂不可用</strong><small>{{ fundamentalsError }}</small></div>
            <button type="button" @click="loadFundamentals(stock.symbol)">重试</button>
          </div>
          <template v-else-if="latestReport">
            <div class="earnings-pulse-metric">
              <span>营业收入</span>
              <strong>{{ compactMoney(latestReport.revenue) }}</strong>
              <small v-if="latestReport.revenue_yoy !== null" :class="riseClass(latestReport.revenue_yoy)">
                同比 {{ signedValue(Number(latestReport.revenue_yoy), '%') }}
              </small>
              <small v-else>同比未披露</small>
            </div>
            <div class="earnings-pulse-metric">
              <span>归母净利润</span>
              <strong>{{ compactMoney(latestReport.net_profit_parent) }}</strong>
              <small v-if="latestReport.net_profit_yoy !== null" :class="riseClass(latestReport.net_profit_yoy)">
                同比 {{ signedValue(Number(latestReport.net_profit_yoy), '%') }}
              </small>
              <small v-else>同比未披露</small>
            </div>
            <div class="earnings-pulse-metric">
              <span>加权 ROE</span>
              <strong>{{ formatMetric(latestReport.weighted_roe, '%') }}</strong>
              <small>{{ fundamentals?.provider_label }} · {{ fundamentals ? cacheLabel(fundamentals.cache_status) : '' }}</small>
            </div>
            <button type="button" class="earnings-pulse-action" @click="selectTab('fundamentals')">
              <span>查看财务报表</span><strong aria-hidden="true">→</strong>
            </button>
          </template>
          <div v-else class="earnings-pulse-state">
            <div><strong>暂无财务报告</strong><small>数据源暂未返回该公司的有效披露记录</small></div>
          </div>
        </section>

        <nav class="drawer-tabs" aria-label="股票详情栏目">
          <button
            :class="{ active: activeTab === 'market' }"
            :aria-selected="activeTab === 'market'"
            @click="selectTab('market')"
          >行情与K线</button>
          <button
            :class="{ active: activeTab === 'fundamentals' }"
            :aria-selected="activeTab === 'fundamentals'"
            @click="selectTab('fundamentals')"
          >财务报表</button>
          <button
            :class="{ active: activeTab === 'events' }"
            :aria-selected="activeTab === 'events'"
            @click="selectTab('events')"
          >业绩事件</button>
          <button
            :class="{ active: activeTab === 'announcements' }"
            :aria-selected="activeTab === 'announcements'"
            @click="selectTab('announcements')"
          >公司动态</button>
        </nav>

        <section v-if="activeTab === 'market'" class="kline-stage">
          <div class="chart-head">
            <div>
              <strong>日 K · 前复权</strong>
              <span>颜色看较昨收涨跌 · 空心阳线 / 实心阴线看开收方向</span>
            </div>
            <div v-if="history" class="history-cache-state" :class="{ complete: history.complete }">
              <i></i>
              <span>
                <strong>{{ history.cached_count }} / {{ history.target_count }}</strong>
                {{ history.complete ? '历史缓存已就绪' : '后台补全中' }}
              </span>
            </div>
          </div>
          <div v-if="loading" class="drawer-state"><span class="loading-dot"></span>正在装载价格结构…</div>
          <div v-else-if="error" class="drawer-state error-state">{{ error }}</div>
          <div v-else-if="chart.bars.length" class="kline-chart-shell">
            <svg
              class="kline-chart"
              viewBox="0 0 820 350"
              role="img"
              tabindex="0"
              :aria-label="`${stock.name}日K线，颜色表示较昨收涨跌，空心和实心表示开收方向；使用左右方向键查看各交易日详情`"
              @focus="selectLatestBar"
              @keydown.left.prevent="moveBarSelection(-1)"
              @keydown.right.prevent="moveBarSelection(1)"
              @pointerleave="clearMouseSelection"
            >
              <g transform="translate(18 0)">
                <g v-for="guide in chart.guides" :key="guide.y">
                  <line x1="0" :y1="guide.y" x2="760" :y2="guide.y" class="chart-grid" />
                  <text x="766" :y="guide.y + 4" class="chart-axis">{{ formatNumber(guide.label) }}</text>
                </g>
                <g
                  v-for="(bar, index) in chart.bars"
                  :key="bar.trade_date"
                  :class="[
                    `candle-${bar.dayDirection}`,
                    `candle-shape-${bar.candleDirection}`,
                    { 'is-hovered': hoveredIndex === index },
                  ]"
                >
                  <line :x1="bar.x" :x2="bar.x" :y1="bar.wickTop" :y2="bar.wickBottom" class="candle-wick" />
                  <rect :x="bar.x - bar.candleWidth / 2" :y="bar.bodyTop" :width="bar.candleWidth" :height="bar.bodyHeight" class="candle-body" />
                  <rect :x="bar.x - bar.candleWidth / 2" :y="bar.volumeY" :width="bar.candleWidth" :height="bar.volumeHeight" class="volume-bar" />
                </g>
                <g v-if="chart.costY !== null" class="cost-line">
                  <line
                    v-if="chart.costOutside === null"
                    x1="0"
                    :y1="chart.costY"
                    x2="760"
                    :y2="chart.costY"
                  />
                  <text
                    x="6"
                    :y="chart.costOutside === 'above' ? chart.costY + 11 : chart.costY - 5"
                  >{{ chart.costOutside === 'above' ? '↑ ' : chart.costOutside === 'below' ? '↓ ' : '' }}持仓成本 {{ formatNumber(position?.average_cost ?? 0, 4) }}{{ chart.costOutside ? '（图外）' : '' }}</text>
                </g>
                <g v-for="bar in chart.dates" :key="`date-${bar.trade_date}`">
                  <text :x="bar.x" y="340" text-anchor="middle" class="chart-date">{{ bar.trade_date.slice(5) }}</text>
                </g>
                <g v-if="hoveredBar" class="kline-cursor" aria-hidden="true">
                  <line :x1="hoveredBar.x" :x2="hoveredBar.x" y1="18" y2="318" />
                  <circle :cx="hoveredBar.x" :cy="hoveredBar.closeY" r="3.2" />
                </g>
                <g class="kline-hit-layer">
                  <rect
                    v-for="(bar, index) in chart.bars"
                    :key="`hit-${bar.trade_date}`"
                    :x="bar.hitX"
                    y="0"
                    :width="bar.hitWidth"
                    height="326"
                    @pointerenter="selectBar(index)"
                    @pointerdown.stop="selectBar(index)"
                  />
                </g>
              </g>
            </svg>
            <aside
              v-if="hoveredBar"
              class="kline-tooltip"
              :class="{ 'align-right': hoveredBar.x > 456 }"
              :style="tooltipStyle"
              role="status"
            >
              <header>
                <div><span>TRADE DAY</span><strong>{{ hoveredBar.trade_date }}</strong></div>
                <b :class="riseClass(hoveredBar.changeAmount ?? 0)">{{ signedValue(hoveredBar.changePercent, '%') }}</b>
              </header>
              <div class="kline-tooltip-deltas">
                <div class="kline-tooltip-delta">
                  <span>较昨收</span>
                  <strong :class="riseClass(hoveredBar.changeAmount ?? 0)">{{ signedValue(hoveredBar.changeAmount) }}</strong>
                  <small>昨收 {{ hoveredBar.previousClose === null ? '—' : formatNumber(hoveredBar.previousClose) }}</small>
                </div>
                <div class="kline-tooltip-delta secondary">
                  <span>开收变化</span>
                  <strong :class="riseClass(hoveredBar.openChangeAmount)">{{ signedValue(hoveredBar.openChangePercent, '%') }}</strong>
                  <small>{{ hoveredBar.candleLabel }}</small>
                </div>
              </div>
              <dl>
                <div><dt>开盘</dt><dd>{{ formatNumber(hoveredBar.open_price) }}</dd></div>
                <div><dt>最高</dt><dd>{{ formatNumber(hoveredBar.high_price) }}</dd></div>
                <div><dt>最低</dt><dd>{{ formatNumber(hoveredBar.low_price) }}</dd></div>
                <div><dt>收盘</dt><dd>{{ formatNumber(hoveredBar.close_price) }}</dd></div>
                <div><dt>振幅</dt><dd>{{ signedValue(hoveredBar.amplitudePercent, '%').replace('+', '') }}</dd></div>
                <div><dt>成交量</dt><dd>{{ compactVolume(hoveredBar.volume) }}</dd></div>
              </dl>
            </aside>
          </div>
          <div v-else class="drawer-state">暂无可用历史行情</div>
        </section>

        <section v-else-if="activeTab === 'fundamentals'" class="fundamentals-stage">
          <div v-if="fundamentalsLoading" class="drawer-state financial-state">
            <span class="loading-dot"></span>正在读取公司报告与财务指标…
          </div>
          <div v-else-if="fundamentalsError" class="drawer-state financial-state error-state">
            <div>
              <strong>财务数据暂时不可用</strong>
              <span>{{ fundamentalsError }}</span>
              <button type="button" @click="loadFundamentals(stock.symbol)">重新读取</button>
            </div>
          </div>
          <template v-else-if="fundamentals && latestReport">
            <header class="fundamental-report-head">
              <div>
                <span class="section-kicker">LATEST FILING</span>
                <strong>{{ latestReport.report_name }}</strong>
                <small>
                  报告期 {{ formatDate(latestReport.report_period) }} ·
                  {{ formatDate(latestReport.announcement_date) }} 公告
                </small>
              </div>
              <div class="fundamental-source-stamp">
                <span>{{ fundamentals.provider_label }}</span>
                <strong>{{ cacheLabel(fundamentals.cache_status) }}</strong>
                <small>{{ formatDateTime(fundamentals.fetched_at) }} 更新</small>
              </div>
            </header>

            <div class="fundamental-metrics">
              <article v-for="metric in metricCards" :key="metric.key">
                <span>{{ metric.label }}</span>
                <strong>{{ metric.value }}</strong>
                <div>
                  <b
                    v-if="metric.delta !== null"
                    :class="riseClass(metric.delta)"
                  >同比 {{ signedValue(Number(metric.delta), '%') }}</b>
                  <small>{{ metric.note }}</small>
                </div>
              </article>
            </div>

            <article class="financial-trend-panel">
              <header>
                <div>
                  <span class="section-kicker">REPORTING TRACK</span>
                  <strong>最近 {{ trendReports.length }} 个报告期</strong>
                  <small>报告期累计披露值，不等同于单季度值</small>
                </div>
                <div class="trend-switch" aria-label="财务趋势指标">
                  <button
                    :class="{ active: trendMetric === 'revenue' }"
                    @click="trendMetric = 'revenue'"
                  >营业收入</button>
                  <button
                    :class="{ active: trendMetric === 'net_profit_parent' }"
                    @click="trendMetric = 'net_profit_parent'"
                  >归母净利润</button>
                </div>
              </header>
              <div v-if="financialTrend.points.length" class="financial-trend-chart">
                <svg viewBox="0 0 820 268" role="img" aria-label="公司财务报告趋势图">
                  <g transform="translate(32 8)">
                    <g v-for="guide in financialTrend.guides" :key="guide.y">
                      <line x1="0" :y1="guide.y" x2="740" :y2="guide.y" />
                      <text x="750" :y="guide.y + 4">{{ compactMoney(guide.value) }}</text>
                    </g>
                    <polyline :points="financialTrend.polyline" />
                    <g v-for="point in financialTrend.points" :key="`${point.label}-${point.x}`">
                      <circle :cx="point.x" :cy="point.y" r="4" />
                      <text :x="point.x" :y="point.y - 10" text-anchor="middle" class="point-value">
                        {{ compactMoney(point.value) }}
                      </text>
                      <text :x="point.x" y="244" text-anchor="middle" class="period-label">
                        {{ point.label }}
                      </text>
                    </g>
                  </g>
                </svg>
              </div>
              <div v-else class="financial-empty">所选指标暂无可用趋势数据</div>
            </article>

            <article class="financial-report-table">
              <header>
                <div><span class="section-kicker">PERIOD COMPARISON</span><strong>报告期对照</strong></div>
                <small>金额单位自动换算 · “—”表示数据源未披露</small>
              </header>
              <div class="financial-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>报告期</th>
                      <th>营业收入</th>
                      <th>归母净利润</th>
                      <th>ROE</th>
                      <th>毛利率</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="report in fundamentals.reports.slice(0, 8)" :key="report.report_period">
                      <td><strong>{{ report.report_name }}</strong><small>{{ formatDate(report.announcement_date) }} 公告</small></td>
                      <td>{{ compactMoney(report.revenue) }}<small v-if="report.revenue_yoy !== null" :class="riseClass(report.revenue_yoy)">同比 {{ signedValue(Number(report.revenue_yoy), '%') }}</small></td>
                      <td>{{ compactMoney(report.net_profit_parent) }}<small v-if="report.net_profit_yoy !== null" :class="riseClass(report.net_profit_yoy)">同比 {{ signedValue(Number(report.net_profit_yoy), '%') }}</small></td>
                      <td>{{ formatMetric(report.weighted_roe, '%') }}</td>
                      <td>{{ formatMetric(report.gross_margin, '%') }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>

            <footer class="fundamental-provenance">
              <span>数据来源：{{ fundamentals.provider_label }}公开财务数据 · 本地规范化缓存</span>
              <a :href="latestReport.source_url" target="_blank" rel="noopener noreferrer">查看来源页面 ↗</a>
            </footer>
          </template>
        </section>

        <section v-else-if="activeTab === 'events'" class="performance-stage">
          <div v-if="fundamentalsLoading" class="drawer-state financial-state">
            <span class="loading-dot"></span>正在整理业绩事件时间轴…
          </div>
          <div v-else-if="fundamentalsError" class="drawer-state financial-state error-state">
            <div>
              <strong>业绩事件暂时不可用</strong>
              <span>{{ fundamentalsError }}</span>
              <button type="button" @click="loadFundamentals(stock.symbol)">重新读取</button>
            </div>
          </div>
          <template v-else-if="fundamentals">
            <header class="performance-head">
              <div>
                <span class="section-kicker">DISCLOSURE TIMELINE</span>
                <strong>业绩披露轨迹</strong>
                <small>预告与正式财报分开标识，按公告日期倒序</small>
              </div>
              <span>{{ fundamentals.events.length }} EVENTS</span>
            </header>
            <div v-if="fundamentals.events.length" class="performance-timeline">
              <article
                v-for="event in fundamentals.events"
                :key="`${event.event_type}-${event.report_period}-${event.announcement_date}`"
                :class="event.event_type.toLowerCase()"
              >
                <time :datetime="event.announcement_date">
                  <strong>{{ formatDate(event.announcement_date).slice(5) }}</strong>
                  <span>{{ event.announcement_date.slice(0, 4) }}</span>
                </time>
                <i aria-hidden="true"></i>
                <div class="performance-event-card">
                  <header>
                    <div>
                      <span>{{ event.event_label }}</span>
                      <strong>{{ event.report_name }}</strong>
                    </div>
                    <b v-if="event.forecast_type">{{ event.forecast_type }}</b>
                  </header>
                  <p>{{ eventSummary(event) }}</p>
                  <dl>
                    <div v-if="event.revenue_growth_lower !== null">
                      <dt>营收同比</dt>
                      <dd :class="growthRangeClass(event.revenue_growth_lower, event.revenue_growth_upper)">{{ growthRange(event.revenue_growth_lower, event.revenue_growth_upper) }}</dd>
                    </div>
                    <div v-if="event.net_profit_growth_lower !== null">
                      <dt>利润同比</dt>
                      <dd :class="growthRangeClass(event.net_profit_growth_lower, event.net_profit_growth_upper)">{{ growthRange(event.net_profit_growth_lower, event.net_profit_growth_upper) }}</dd>
                    </div>
                  </dl>
                  <details v-if="event.reason">
                    <summary>查看业绩变动说明</summary>
                    <p>{{ event.reason }}</p>
                  </details>
                  <a :href="event.source_url" target="_blank" rel="noopener noreferrer">查看来源页面 ↗</a>
                </div>
              </article>
            </div>
            <div v-else class="drawer-state financial-state">暂无业绩预告或正式财报事件</div>
            <footer class="fundamental-provenance">
              <span>正式财报与业绩预告不是同一口径，请以最终公告为准</span>
              <small>{{ cacheLabel(fundamentals.cache_status) }}</small>
            </footer>
          </template>
        </section>

        <section v-else-if="activeTab === 'announcements'" class="announcement-stage">
          <div
            v-if="announcementsLoading && !announcements && !regulatoryLetters"
            class="drawer-state financial-state"
          >
            <span class="loading-dot"></span>正在核对公司公告与监管函件…
          </div>
          <div
            v-else-if="!announcements && !regulatoryLetters && companyDynamicsErrors.length"
            class="drawer-state financial-state error-state"
          >
            <div>
              <strong>公司动态暂时不可用</strong>
              <span>{{ companyDynamicsErrors.join('；') }}</span>
              <button type="button" @click="loadAnnouncements(stock.symbol)">重新读取</button>
            </div>
          </div>
          <template v-else-if="announcements || regulatoryLetters">
            <header class="announcement-head">
              <div>
                <span class="section-kicker">OFFICIAL DISCLOSURE LEDGER</span>
                <strong>公司动态档案</strong>
                <small>公告、监管函与回复按日期合并 · 原文均可追溯</small>
              </div>
              <div
                class="announcement-source-stamp"
                :class="{ stale: companyDynamicsCacheStatus === 'STALE' }"
              >
                <span>{{ companyDynamicsProvider }}</span>
                <strong>{{ cacheLabel(companyDynamicsCacheStatus) }}</strong>
                <small>{{ formatDateTime(companyDynamicsFetchedAt) }} 更新</small>
              </div>
            </header>

            <div class="announcement-toolbar">
              <div class="announcement-range" aria-label="公告时间范围">
                <button
                  v-for="range in announcementRanges"
                  :key="range.value"
                  type="button"
                  :class="{ active: announcementRange === range.value }"
                  :aria-pressed="announcementRange === range.value"
                  :disabled="announcementsLoading"
                  @click="selectAnnouncementRange(range.value)"
                >{{ range.label }}</button>
              </div>
              <span>
                {{ formatDate(companyDynamicsRangeStart) }} — {{ formatDate(companyDynamicsRangeEnd) }}
                · {{ companyDynamics.length }} 条动态
              </span>
            </div>

            <div v-if="companyDynamicsCacheStatus === 'STALE'" class="announcement-stale-note" role="status">
              交易所当前响应异常，以下内容来自最近一次成功缓存；打开原文时请再次核对发布日期。
            </div>
            <div v-if="companyDynamicsErrors.length" class="announcement-partial-note" role="status">
              部分官方目录暂时不可用：{{ companyDynamicsErrors.join('；') }}。下方仍展示已成功取得的内容。
            </div>

            <div v-if="companyDynamics.length" class="announcement-ledger" :aria-busy="announcementsLoading">
              <article
                v-for="item in companyDynamics"
                :key="item.id"
                :class="{ 'regulatory-entry': item.kind === 'regulatory' }"
              >
                <time :datetime="item.date">
                  <strong>{{ formatDate(item.date).slice(5) }}</strong>
                  <span>{{ item.date.slice(0, 4) }}</span>
                </time>
                <i aria-hidden="true"></i>
                <a
                  v-if="item.kind === 'announcement'"
                  class="announcement-document"
                  :href="item.announcement.source_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :aria-label="`打开${item.announcement.title}官方 PDF`"
                >
                  <header>
                    <span class="exchange-seal">{{ exchangeLabel(item.announcement.exchange) }}</span>
                    <span>{{ announcementTypeLabel(item.announcement) }}</span>
                    <span>官方 PDF</span>
                  </header>
                  <h3>{{ item.announcement.title }}</h3>
                  <footer>
                    <small>文件标识 {{ item.announcement.external_id }}</small>
                    <strong>查看交易所原文 ↗</strong>
                  </footer>
                </a>
                <div v-else class="announcement-document regulatory-document">
                  <header>
                    <span class="exchange-seal">{{ exchangeLabel(item.letter.exchange) }}</span>
                    <span>{{ item.letter.letter_type }}</span>
                    <span class="regulatory-mark">监管函</span>
                  </header>
                  <h3>{{ item.letter.title }}</h3>
                  <div class="regulatory-replies">
                    <header>
                      <strong :class="{ pending: item.letter.reply_status === 'NO_REPLY_FOUND' }">
                        {{ item.letter.reply_status === 'REPLIED'
                          ? `已关联 ${item.letter.replies.length} 份回复`
                          : '未发现公开回复' }}
                      </strong>
                      <small>仅表示当前官方目录检索结果</small>
                    </header>
                    <a
                      v-for="reply in item.letter.replies"
                      :key="reply.external_id"
                      :href="reply.source_url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <span>{{ reply.title }}</span>
                      <small>
                        {{ reply.reply_date ? formatDate(reply.reply_date) : '回复日期以原文为准' }}
                        · {{ replyRelationLabel(reply.match_method) }} ↗
                      </small>
                    </a>
                    <p v-if="!item.letter.replies.length">
                      可能尚未回复、回复未公开，或当前查询范围未覆盖；不能据此判断公司逾期。
                    </p>
                  </div>
                  <footer>
                    <small>函件标识 {{ item.letter.external_id }}</small>
                    <a
                      :href="item.letter.source_url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >查看监管函原文 ↗</a>
                  </footer>
                </div>
              </article>
            </div>
            <div v-else class="announcement-empty">
              <span>NO FILINGS IN RANGE</span>
              <strong>所选时间范围内暂无公司公告或监管函件</strong>
              <small v-if="announcementRange !== 365">可以切换到“1年”继续查找历史动态。</small>
              <small v-else>交易所暂未返回该公司的有效动态记录。</small>
            </div>

            <button
              v-if="canLoadMoreAnnouncements"
              type="button"
              class="announcement-more"
              :disabled="announcementsLoading"
              @click="loadMoreAnnouncements"
            >
              <span v-if="announcementsLoading" class="loading-dot"></span>
              {{ announcementsLoading ? '正在读取更多动态' : '查看更多动态' }}
            </button>

            <footer class="announcement-provenance">
              <span>监管函与回复关系可展开核对；正文尚未进入 AI 分析或向量检索。</span>
              <strong>{{ stock.exchange }} · OFFICIAL SOURCE</strong>
            </footer>
          </template>
        </section>

        <section v-if="position" class="drawer-position">
          <div><span>当前持仓</span><strong>{{ position.quantity.toLocaleString('zh-CN') }} 股</strong></div>
          <div><span>T+1 可卖</span><strong>{{ position.sellable_quantity.toLocaleString('zh-CN') }} 股</strong></div>
          <div><span>持仓成本</span><strong>¥ {{ formatNumber(position.average_cost, 4) }}</strong></div>
          <div><span>浮动盈亏</span><strong :class="riseClass(position.profit_loss)">{{ Number(position.profit_loss) >= 0 ? '+' : '' }}{{ formatNumber(position.profit_loss) }}</strong></div>
        </section>

        <footer class="drawer-actions">
          <button class="drawer-buy" @click="emit('prepareOrder', 'BUY')">装载买入票据</button>
          <button class="drawer-sell" :disabled="!position?.sellable_quantity" @click="emit('prepareOrder', 'SELL')">装载卖出票据</button>
        </footer>
      </aside>
    </div>
  </Transition>
</template>
