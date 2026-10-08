<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import type { DailySnapshot, DayReview, ReviewTrade } from '../types'
import { formatNumber, shortDateTime } from '../utils/formatters'

const snapshots = ref<DailySnapshot[]>([])
const selectedDate = ref('')
const review = ref<DayReview | null>(null)
const dailyDraft = ref('')
const tradeDrafts = ref<Record<string, string>>({})
const loading = ref(true)
const detailLoading = ref(false)
const saving = ref('')
const error = ref('')
const feedback = ref('')
let detailRequest = 0

const selected = computed(() => review.value?.snapshot ?? null)
const completeDays = computed(() => snapshots.value.filter((item) => item.total_assets !== null))
const maxDate = computed(() => snapshots.value.at(-1)?.date ?? '')
const recentDays = computed(() => [...snapshots.value].reverse().slice(0, 14))
const chart = computed(() => {
  const data = snapshots.value
  const values = completeDays.value.map((item) => Number(item.total_assets))
  if (!data.length || !values.length) return { segments: [] as string[], dots: [] as { x: number; y: number; date: string }[] }
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const span = Math.max(maximum - minimum, Math.max(Math.abs(maximum), 1) * 0.002)
  const dots: { x: number; y: number; date: string }[] = []
  const segments: string[] = []
  let active = ''
  data.forEach((item, index) => {
    if (item.total_assets === null) {
      if (active) segments.push(active)
      active = ''
      return
    }
    const x = data.length === 1 ? 400 : 34 + index * 732 / (data.length - 1)
    const y = 198 - (Number(item.total_assets) - minimum) / span * 156
    active += `${active ? ' L' : 'M'} ${x.toFixed(1)} ${y.toFixed(1)}`
    dots.push({ x, y, date: item.date })
  })
  if (active) segments.push(active)
  return { segments, dots }
})

function signed(value: string | null) {
  if (value === null) return '—'
  return `${Number(value) > 0 ? '+' : ''}${formatNumber(value)}`
}

async function loadSnapshots() {
  loading.value = true
  error.value = ''
  try {
    const previous = selectedDate.value
    snapshots.value = await api.reviewSnapshots(90)
    if (snapshots.value.length) {
      const nextDate = previous && previous <= snapshots.value.at(-1)!.date
        ? previous : snapshots.value.at(-1)!.date
      if (selectedDate.value === nextDate) void loadSelectedDay(nextDate)
      else selectedDate.value = nextDate
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '账户快照加载失败'
  } finally {
    loading.value = false
  }
}

async function loadSelectedDay(date: string) {
  const request = ++detailRequest
  review.value = null
  feedback.value = ''
  if (!date) return
  detailLoading.value = true
  error.value = ''
  try {
    const result = await api.dayReview(date)
    if (request !== detailRequest) return
    review.value = result
    dailyDraft.value = result.daily_note ?? ''
    tradeDrafts.value = Object.fromEntries(result.trades.map((trade) => [trade.trade_no, trade.note ?? '']))
  } catch (reason) {
    if (request === detailRequest) error.value = reason instanceof Error ? reason.message : '复盘明细加载失败'
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

watch(selectedDate, loadSelectedDay)

async function saveDailyNote() {
  if (!selectedDate.value || !review.value) return
  const date = selectedDate.value
  const content = dailyDraft.value
  saving.value = 'daily'
  error.value = ''
  try {
    const result = await api.saveDayReviewNote(date, content)
    if (selectedDate.value === date && review.value) {
      review.value.daily_note = result.content
      feedback.value = '当日复盘已保存'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '复盘保存失败'
  } finally {
    saving.value = ''
  }
}

async function saveTradeNote(trade: ReviewTrade) {
  saving.value = trade.trade_no
  error.value = ''
  try {
    const result = await api.saveTradeNote(trade.trade_no, tradeDrafts.value[trade.trade_no] ?? '')
    trade.note = result.content
    feedback.value = `${trade.symbol} 成交笔记已保存`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '成交笔记保存失败'
  } finally {
    saving.value = ''
  }
}

onMounted(() => void loadSnapshots())
</script>

<template>
  <section class="review-workspace" aria-label="每日学习复盘">
    <header class="review-heading">
      <div>
        <span class="section-kicker">DAILY ACCOUNT REVIEW</span>
        <h2>每日账户复盘</h2>
        <p>以成交与资金流水重建账户，用未复权收盘价估值。仅展示已结束的日期。</p>
      </div>
      <div class="review-heading-actions">
        <label class="review-date-picker">选择日期
          <input v-model="selectedDate" type="date" :max="maxDate || undefined" :disabled="loading || !snapshots.length" />
        </label>
        <button type="button" :disabled="loading" @click="loadSnapshots">{{ loading ? '核算中…' : '刷新估值' }}</button>
      </div>
    </header>

    <p v-if="error" class="review-alert" role="alert">{{ error }}</p>
    <p v-if="feedback" class="review-feedback" role="status">{{ feedback }}</p>
    <div v-if="loading" class="review-empty">正在重建账户账簿…</div>
    <div v-else-if="!snapshots.length" class="review-empty">尚无已结束的账户日期。次日即可查看第一份每日快照。</div>
    <template v-else>
      <div class="review-curve-panel">
        <div class="review-panel-title"><strong>资产曲线</strong><small>最近 {{ snapshots.length }} 天 · 缺少历史价格的日期留空</small></div>
        <svg class="review-curve" viewBox="0 0 800 230" role="img" aria-label="最近九十天账户总资产曲线">
          <line x1="34" y1="198" x2="766" y2="198" class="review-axis" />
          <line x1="34" y1="42" x2="766" y2="42" class="review-gridline" />
          <path v-for="(segment, index) in chart.segments" :key="index" :d="segment" class="review-line" />
          <circle v-for="dot in chart.dots" :key="dot.date" :cx="dot.x" :cy="dot.y" :r="dot.date === selectedDate ? 5 : 2.5" :class="['review-dot', { selected: dot.date === selectedDate }]" />
        </svg>
        <div class="review-curve-labels"><span>{{ snapshots[0]?.date }}</span><span>{{ snapshots.at(-1)?.date }}</span></div>
      </div>

      <div class="review-day-strip" aria-label="最近账户日期">
        <button v-for="day in recentDays" :key="day.date" type="button" :class="{ active: selectedDate === day.date }" @click="selectedDate = day.date">
          <span>{{ day.date.slice(5) }}</span><strong>{{ day.total_assets === null ? '待估值' : `¥ ${formatNumber(day.total_assets)}` }}</strong>
        </button>
      </div>

      <div v-if="detailLoading" class="review-empty">正在读取 {{ selectedDate }} 的成交与持仓…</div>
      <template v-else-if="selected">
        <div class="review-equation">
          <div class="review-equation-head"><span class="section-kicker">{{ selected.date }} · 账户对账</span><strong>{{ selected.total_assets === null ? '历史价格待补齐' : `¥ ${formatNumber(selected.total_assets)}` }}</strong></div>
          <div class="review-equation-grid">
            <div><span>当日资产变化</span><strong>{{ signed(selected.asset_change) }}</strong></div>
            <span class="review-symbol">=</span>
            <div><span>现金变化</span><strong>{{ signed(selected.cash_change) }}</strong></div>
            <span class="review-symbol">+</span>
            <div><span>持仓市值变化</span><strong>{{ signed(selected.market_value_change) }}</strong></div>
          </div>
          <div class="review-reconcile-status">
            <span>现金 ¥ {{ formatNumber(selected.cash) }}</span>
            <span>持仓市值 {{ selected.market_value === null ? '—' : `¥ ${formatNumber(selected.market_value)}` }}</span>
            <span>已实现盈亏 {{ signed(selected.realized_pnl) }}</span>
            <span>浮动盈亏 {{ signed(selected.floating_pnl) }}</span>
            <span>当日 {{ selected.trade_count }} 笔成交 · 手续费 ¥ {{ formatNumber(selected.fees) }}</span>
          </div>
          <p v-if="!selected.ledger_consistent || selected.reconciliation_delta !== null && Number(selected.reconciliation_delta) !== 0 || selected.asset_change_delta !== null && Number(selected.asset_change_delta) !== 0" class="review-alert">
            对账异常：资金流水差额 {{ signed(selected.ledger_cash_delta) }}；资产与盈亏差额 {{ signed(selected.reconciliation_delta) }}；当日资产变化差额 {{ signed(selected.asset_change_delta) }}。
          </p>
          <p v-else-if="selected.valuation_status === 'MISSING'" class="review-alert">缺少 {{ selected.missing_symbols.join('、') }} 的历史价格；该日资产与浮动盈亏暂不计算。</p>
          <p v-else-if="selected.valuation_status === 'PROVISIONAL'" class="review-footnote">部分估值来自原始行情快照，尚待未复权历史收盘价核对；后台补齐后会重新计算。</p>
          <p v-else-if="selected.valuation_status === 'CARRIED'" class="review-footnote">部分持仓沿用最近有记录的收盘价；请查看下方各持仓的价格日期。</p>
          <p v-else class="review-footnote">现金变化与当日成交净资金流 {{ signed(selected.trade_cash_flow) }} 对应；总盈亏 = 已实现盈亏 + 浮动盈亏。</p>
        </div>

        <div class="review-detail-grid">
          <section class="review-panel">
            <div class="review-panel-title"><strong>收盘持仓</strong><small>成本与历史估值</small></div>
            <p v-if="!selected.positions.length" class="review-empty">当日无持仓</p>
            <div v-else class="review-table-wrap"><table><thead><tr><th>股票</th><th>数量</th><th>成本</th><th>估值价格 / 日期</th><th>市值</th><th>浮动盈亏</th></tr></thead><tbody>
              <tr v-for="position in selected.positions" :key="position.symbol"><td><strong>{{ position.stock_name }}</strong><small>{{ position.symbol }}</small></td><td>{{ position.quantity }}</td><td>{{ formatNumber(position.cost_basis) }}</td><td>{{ position.price === null ? '—' : formatNumber(position.price) }}<small>{{ position.price_date ?? '待补' }} {{ position.price_source === 'SNAPSHOT' ? '· 快照' : '' }}</small></td><td>{{ position.market_value === null ? '—' : formatNumber(position.market_value) }}</td><td>{{ signed(position.floating_pnl) }}</td></tr>
            </tbody></table></div>
          </section>

          <section class="review-panel">
            <div class="review-panel-title"><strong>当日成交与笔记</strong><small>{{ review?.trades.length ?? 0 }} 笔</small></div>
            <p v-if="!review?.trades.length" class="review-empty">当日无成交，可在下方记录观察与复盘。</p>
            <article v-for="trade in review?.trades ?? []" :key="trade.trade_no" class="review-trade">
              <div class="review-trade-head"><strong>{{ trade.side === 'BUY' ? '买入' : '卖出' }} {{ trade.stock_name }} <small>{{ trade.symbol }}</small></strong><span>{{ shortDateTime(trade.created_at) }}</span></div>
              <p>{{ trade.quantity }} 股 × ¥ {{ formatNumber(trade.price) }} · 资金 {{ signed(trade.cash_flow) }} · 费用 ¥ {{ formatNumber(trade.fee) }}</p>
              <label>交易笔记<textarea v-model="tradeDrafts[trade.trade_no]" maxlength="2000" rows="2" placeholder="记录下单依据、预期与之后要验证的条件"></textarea></label>
              <button type="button" :disabled="saving === trade.trade_no" @click="saveTradeNote(trade)">{{ saving === trade.trade_no ? '保存中…' : '保存笔记' }}</button>
            </article>
          </section>
        </div>

        <section class="review-panel review-daily-note">
          <div class="review-panel-title"><strong>当日复盘</strong><small>记录结果、原因和下一步观察点</small></div>
          <textarea v-model="dailyDraft" maxlength="2000" rows="4" placeholder="今天的资产变化由什么驱动？哪些判断值得保留或修正？"></textarea>
          <button type="button" :disabled="saving === 'daily'" @click="saveDailyNote">{{ saving === 'daily' ? '保存中…' : '保存当日复盘' }}</button>
        </section>
      </template>
    </template>
  </section>
</template>
