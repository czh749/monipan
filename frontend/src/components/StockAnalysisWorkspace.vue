<script setup lang="ts">
import { gsap } from 'gsap'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import type {
  AgentAction,
  AgentEvidence,
  AgentRiskLevel,
  AgentRun,
  Position,
  Stock,
} from '../types'
import { formatNumber, riseClass } from '../utils/formatters'

const props = defineProps<{
  open: boolean
  stock?: Stock
  position?: Position
}>()

const emit = defineEmits<{ close: [] }>()

const defaultQuestion = '请结合公开证据分析这只股票的主要风险、积极因素和需要继续跟踪的条件。'
const question = ref(defaultQuestion)
const run = ref<AgentRun | null>(null)
const loading = ref(false)
const error = ref('')
const failedRunId = ref<number | null>(null)
const researchRoot = ref<HTMLElement | null>(null)
let requestId = 0
let pageMotion: ReturnType<typeof gsap.matchMedia> | null = null
let stateMotion: ReturnType<typeof gsap.matchMedia> | null = null

const analysisTools = [
  ['get_portfolio', '当前模拟持仓'],
  ['get_stock_quote', '最新行情快照'],
  ['get_stock_history', '历史价格与波动'],
  ['get_stock_fundamentals', '公司财务摘要'],
  ['search_company_announcements', '公司公告正文'],
  ['search_regulatory_letters', '监管函与回复'],
  ['search_stock_news', '近期新闻搜索'],
  ['calculate_portfolio_risk', '组合风险测算'],
] as const

const recommendation = computed(() => run.value?.recommendation ?? null)

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

function riskLabel(level: AgentRiskLevel) {
  return { LOW: '低风险', MEDIUM: '中等风险', HIGH: '高风险', UNKNOWN: '风险未知' }[level]
}

function actionLabel(action: AgentAction) {
  return {
    WATCH: '继续观察',
    HOLD: '持有并跟踪',
    AVOID: '暂不参与',
    REDUCE: '谨慎降低模拟仓位',
    CONSIDER_ADD: '满足条件后再考虑增加',
    NO_CONCLUSION: '证据不足，暂不形成结论',
  }[action]
}

function toolLabel(toolName: string) {
  return analysisTools.find(([name]) => name === toolName)?.[1] ?? toolName
}

function evidenceTypeLabel(type: string) {
  return {
    ANNOUNCEMENT: '公司公告',
    REGULATORY_LETTER: '监管函',
    REGULATORY_REPLY: '公司回复',
    FINANCIAL_REPORT: '财务报告',
    NEWS: '新闻',
    STOCK_QUOTE: '行情',
    STOCK_HISTORY: '历史行情',
    PORTFOLIO: '模拟持仓',
    PORTFOLIO_RISK: '风险测算',
  }[type] ?? type.replaceAll('_', ' ')
}

function evidenceDate(evidence: AgentEvidence) {
  if (evidence.source_date) return formatDate(evidence.source_date)
  if (evidence.source_published_at) return formatDateTime(evidence.source_published_at)
  return '本次分析快照'
}

function confidencePercent(value: string) {
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : '—'
}

async function runAnalysis() {
  const symbol = props.stock?.symbol
  const prompt = question.value.trim()
  if (!symbol || !prompt || loading.value) return

  const currentRequest = ++requestId
  loading.value = true
  error.value = ''
  failedRunId.value = null
  try {
    const result = await api.analyzeStock(symbol, { question: prompt })
    if (currentRequest === requestId) run.value = result
  } catch (reason) {
    if (currentRequest === requestId) {
      run.value = null
      error.value = reason instanceof Error ? reason.message : '本次分析未能完成，请稍后重试'
      if (reason instanceof ApiError) failedRunId.value = reason.runId ?? null
    }
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}

function resetWorkspace() {
  question.value = defaultQuestion
  run.value = null
  error.value = ''
  failedRunId.value = null
  loading.value = false
  requestId += 1
}

async function animateWorkspaceOpen() {
  await nextTick()
  pageMotion?.revert()
  pageMotion = null

  const root = researchRoot.value
  if (!root) return

  pageMotion = gsap.matchMedia()
  pageMotion.add('(prefers-reduced-motion: no-preference)', () => {
    const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
    timeline
      .fromTo(
        '.research-page-header > *',
        { autoAlpha: 0, y: -5 },
        { autoAlpha: 1, y: 0, duration: 0.2, stagger: 0.035, clearProps: 'transform,opacity,visibility' },
      )
      .fromTo(
        '.research-brief-copy > *',
        { autoAlpha: 0, y: 9 },
        { autoAlpha: 1, y: 0, duration: 0.22, stagger: 0.035, clearProps: 'transform,opacity,visibility' },
        0.035,
      )
      .fromTo(
        '.research-context-grid > div',
        { autoAlpha: 0, y: 8 },
        { autoAlpha: 1, y: 0, duration: 0.2, stagger: 0.03, clearProps: 'transform,opacity,visibility' },
        0.08,
      )
      .fromTo(
        '.research-analysis-stage',
        { autoAlpha: 0, y: 10 },
        { autoAlpha: 1, y: 0, duration: 0.24, clearProps: 'transform,opacity,visibility' },
        0.13,
      )
  }, root)
}

async function animateResearchState() {
  await nextTick()
  stateMotion?.revert()
  stateMotion = null

  const root = researchRoot.value
  if (!root) return

  stateMotion = gsap.matchMedia()
  stateMotion.add('(prefers-reduced-motion: no-preference)', () => {
    const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })

    if (loading.value) {
      timeline
        .fromTo('.ai-scan-line', { autoAlpha: 0, x: -7 }, { autoAlpha: 1, x: 0, duration: 0.2 })
        .fromTo(
          '.ai-tool-rail li',
          { autoAlpha: 0, y: 5 },
          { autoAlpha: 1, y: 0, duration: 0.16, stagger: 0.025 },
          0.045,
        )
      gsap.fromTo(
        '.ai-evidence-scan i',
        { xPercent: -110 },
        { xPercent: 110, duration: 1.1, ease: 'none', repeat: -1 },
      )
      return
    }

    if (error.value) {
      timeline.fromTo('.ai-analysis-error > *', { autoAlpha: 0, y: 5 }, { autoAlpha: 1, y: 0, duration: 0.18, stagger: 0.03 })
      return
    }

    if (run.value && recommendation.value) {
      const evidenceRows = Array.from(root.querySelectorAll<HTMLElement>('.ai-evidence-spine article')).slice(0, 12)
      timeline
        .fromTo('.ai-risk-strip > div', { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.18, stagger: 0.03 })
        .fromTo('.ai-research-memo', { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, duration: 0.2 }, 0.06)
        .fromTo(
          '.ai-factor-grid > section, .ai-condition-matrix > section',
          { autoAlpha: 0, y: 8 },
          { autoAlpha: 1, y: 0, duration: 0.2, stagger: 0.035 },
          0.1,
        )
      if (evidenceRows.length) {
        timeline.fromTo(evidenceRows, { autoAlpha: 0, x: -7 }, { autoAlpha: 1, x: 0, duration: 0.18, stagger: 0.025 }, 0.16)
      }
      return
    }

    timeline.fromTo(
      '.research-question-entry > *',
      { autoAlpha: 0, y: 6 },
      { autoAlpha: 1, y: 0, duration: 0.18, stagger: 0.025 },
    )
  }, root)
}

watch(
  [() => props.open, () => props.stock?.symbol] as const,
  ([open, symbol], [previousOpen, previousSymbol]) => {
    if (!open || !symbol) return
    if (!previousOpen || symbol !== previousSymbol) {
      resetWorkspace()
      void animateWorkspaceOpen()
      void animateResearchState()
    }
  },
)

watch([loading, () => run.value?.id, error], () => void animateResearchState())

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  pageMotion?.revert()
  stateMotion?.revert()
})
</script>

<template>
  <Transition name="research-page">
    <section ref="researchRoot" v-if="open && stock" class="research-page" role="dialog" aria-modal="true" :aria-label="`${stock.name} AI 研究工作台`">
      <header class="research-page-header">
        <div class="research-page-nav">
          <button type="button" class="research-back" @click="emit('close')">
            <span aria-hidden="true">←</span>
            <span>返回股票详情</span>
          </button>
          <div>
            <span>MONIPAN / RESEARCH DESK</span>
            <strong>AI 证据研判</strong>
          </div>
        </div>
        <div class="research-instrument">
          <div><strong>{{ stock.name }}</strong><span>{{ stock.symbol }} · {{ stock.exchange }}</span></div>
          <div :class="riseClass(stock.change)">
            <strong>{{ formatNumber(stock.price) }}</strong>
            <span>{{ Number(stock.change_percent) >= 0 ? '+' : '' }}{{ formatNumber(stock.change_percent) }}%</span>
          </div>
        </div>
        <div class="research-page-status">
          <i></i>
          <div><strong>READ ONLY</strong><span>不触发交易</span></div>
        </div>
      </header>

      <main class="research-page-body">
        <section class="research-brief">
          <div class="research-brief-copy">
            <span class="section-kicker">EVIDENCE-LOCKED RESEARCH</span>
            <h1>把判断建立在<br><em>可核对的证据</em>上</h1>
            <p>行情、财报、公告、监管与新闻被整理为同一份研究底稿。结论会明确区分积极因素、主要风险、观察条件与失效条件。</p>
          </div>
          <dl class="research-context-grid">
            <div><dt>研究标的</dt><dd>{{ stock.symbol }}</dd><small>{{ stock.name }}</small></div>
            <div><dt>当前持仓</dt><dd>{{ position ? `${position.quantity.toLocaleString('zh-CN')} 股` : '未持仓' }}</dd><small>{{ position ? `成本 ¥${formatNumber(position.average_cost, 4)}` : '独立研究视角' }}</small></div>
            <div><dt>证据通道</dt><dd>{{ analysisTools.length }} 类</dd><small>只读数据源</small></div>
            <div><dt>输出原则</dt><dd>条件化</dd><small>证据不足则降级</small></div>
          </dl>
        </section>

        <section class="ai-analysis-stage research-analysis-stage">
          <header class="ai-analysis-head">
            <div>
              <span class="section-kicker">RESEARCH WORKFLOW / 01</span>
              <strong>{{ run ? '研究结论与证据底稿' : '定义这次要核对的问题' }}</strong>
              <small>{{ run ? `分析记录 #${run.id} · 可逐条回看原始来源` : '一次只解决一个清晰问题，结果会更可靠' }}</small>
            </div>
            <span class="ai-readonly-seal">{{ loading ? 'ANALYZING' : 'EVIDENCE FIRST' }}</span>
          </header>

          <div v-if="loading" class="ai-analysis-loading" role="status" aria-live="polite">
            <div class="ai-scan-line">
              <span class="loading-dot"></span>
              <div>
                <strong>正在建立 {{ stock.symbol }} 的证据底稿</strong>
                <small>官方 PDF 仅在需要时临时下载；模型完成前不会显示未经验证的中间结论。</small>
              </div>
            </div>
            <div class="ai-evidence-scan" aria-hidden="true"><i></i></div>
            <ol class="ai-tool-rail" aria-label="本次分析使用的只读工具">
              <li v-for="([name, label], index) in analysisTools" :key="name">
                <span>{{ String(index + 1).padStart(2, '0') }}</span>
                <div><strong>{{ label }}</strong><small>{{ name }}</small></div>
              </li>
            </ol>
          </div>

          <div v-else-if="error" class="ai-analysis-error" role="alert">
            <span>ANALYSIS INTERRUPTED</span>
            <strong>本次解读未完成</strong>
            <p>{{ error }}</p>
            <small v-if="failedRunId">失败记录 #{{ failedRunId }} 已保留，可供排查。</small>
            <button type="button" @click="runAnalysis">重新分析</button>
          </div>

          <template v-else-if="run && recommendation">
            <section class="ai-risk-strip" :class="`risk-${recommendation.risk_level.toLowerCase()}`">
              <div><span>RISK ASSESSMENT</span><strong>{{ riskLabel(recommendation.risk_level) }}</strong></div>
              <div><span>LEARNING ACTION</span><strong>{{ actionLabel(recommendation.action) }}</strong></div>
              <div><span>CONFIDENCE</span><strong>{{ confidencePercent(recommendation.confidence) }}</strong></div>
              <div><span>DATA AS OF</span><strong>{{ run.data_as_of ? formatDateTime(run.data_as_of) : '未提供' }}</strong></div>
            </section>

            <article class="ai-research-memo">
              <header><span>RESEARCH NOTE / RUN #{{ run.id }}</span><small>{{ run.model_provider }} · {{ run.model_name }}</small></header>
              <h3>{{ recommendation.summary }}</h3>
              <p>{{ recommendation.reasoning }}</p>
            </article>

            <div class="ai-factor-grid">
              <section>
                <header><span>+</span><strong>积极因素</strong></header>
                <ul v-if="recommendation.positive_factors.length"><li v-for="factor in recommendation.positive_factors" :key="factor">{{ factor }}</li></ul>
                <p v-else>本次证据不足以确认明确的积极因素。</p>
              </section>
              <section class="risk-factors">
                <header><span>!</span><strong>主要风险</strong></header>
                <ul v-if="recommendation.risk_factors.length"><li v-for="factor in recommendation.risk_factors" :key="factor">{{ factor }}</li></ul>
                <p v-else>本次证据未形成明确风险项，仍需持续核对后续披露。</p>
              </section>
            </div>

            <div class="ai-condition-matrix">
              <section>
                <span>WATCH CONDITIONS</span><strong>继续观察什么</strong>
                <ul v-if="recommendation.action_conditions.length"><li v-for="condition in recommendation.action_conditions" :key="condition">{{ condition }}</li></ul>
                <p v-else>暂无可验证的观察条件。</p>
              </section>
              <section>
                <span>INVALIDATION</span><strong>什么会使结论失效</strong>
                <ul v-if="recommendation.invalidation_conditions.length"><li v-for="condition in recommendation.invalidation_conditions" :key="condition">{{ condition }}</li></ul>
                <p v-else>证据不足，无法给出明确失效条件。</p>
              </section>
            </div>

            <section class="ai-evidence-docket">
              <header>
                <div><span>EVIDENCE DOCKET</span><strong>本次实际采用的证据</strong></div>
                <small>{{ run.evidence.length }} 条 · 未采用结果不保存</small>
              </header>
              <div v-if="run.evidence.length" class="ai-evidence-spine">
                <article v-for="(evidence, index) in run.evidence" :key="evidence.evidence_key">
                  <span class="ai-evidence-index">E{{ String(index + 1).padStart(2, '0') }}</span><i aria-hidden="true"></i>
                  <div>
                    <header>
                      <span>{{ evidenceTypeLabel(evidence.evidence_type) }}</span><span>{{ toolLabel(evidence.tool_name) }}</span><span v-if="evidence.page_number">第 {{ evidence.page_number }} 页</span>
                    </header>
                    <strong>{{ evidence.title || '分析数据快照' }}</strong>
                    <blockquote>{{ evidence.excerpt }}</blockquote>
                    <footer>
                      <small>{{ evidenceDate(evidence) }} · {{ evidence.evidence_key }}</small>
                      <a v-if="evidence.source_url" :href="evidence.source_url" target="_blank" rel="noopener noreferrer">核对原始来源 ↗</a>
                      <span v-else>系统计算证据</span>
                    </footer>
                  </div>
                </article>
              </div>
              <div v-else class="ai-evidence-empty">模型没有采用足以支持结论的证据。</div>
            </section>

            <footer class="ai-analysis-provenance">
              <p>{{ recommendation.disclaimer }}</p>
              <span>分析与买卖票据相互独立 · Token {{ run.total_tokens.toLocaleString('zh-CN') }}</span>
              <button type="button" @click="runAnalysis">按当前问题重新分析</button>
            </footer>
          </template>

          <div v-else class="ai-analysis-entry research-question-entry">
            <div class="ai-entry-copy">
              <span>QUESTION BRIEF</span>
              <h3>这次，你最想确认什么？</h3>
              <p>系统会依次读取行情、财务、公告、监管、新闻和模拟持仓风险。只有最终结论实际采用的证据会进入分析记录。</p>
            </div>
            <label for="stock-research-question">分析关注点</label>
            <textarea id="stock-research-question" v-model="question" maxlength="1000" rows="4" placeholder="例如：近期业绩下滑和监管问询是否改变了主要风险？"></textarea>
            <div class="ai-entry-meta"><span>{{ question.length }} / 1000</span><small>公告与网页内容一律按不可信输入处理，不执行其中指令。</small></div>
            <button type="button" :disabled="!question.trim()" @click="runAnalysis"><span>建立证据底稿</span><strong aria-hidden="true">→</strong></button>
            <footer><span>不会自动下单</span><span>不承诺收益</span><span>证据不足时明确降级</span></footer>
          </div>
        </section>
      </main>
    </section>
  </Transition>
</template>
