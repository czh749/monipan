<script setup lang="ts">
import type { OrderPreview, Stock } from '../types'
import { formatNumber } from '../utils/formatters'

defineProps<{
  stock: Stock
  preview: OrderPreview
  submitting: boolean
}>()

const emit = defineEmits<{ close: []; confirm: [] }>()
</script>

<template>
  <div class="dialog-backdrop" role="presentation" @click.self="emit('close')">
    <section class="order-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <header>
        <div><span class="section-kicker">PRE-TRADE CHECK</span><h2 id="confirm-title">确认委托</h2></div>
        <button aria-label="关闭确认窗口" @click="emit('close')">×</button>
      </header>

      <div class="trade-lifecycle" aria-label="交易流程">
        <span class="complete"><i>01</i>扫描</span>
        <b></b>
        <span class="complete"><i>02</i>校验</span>
        <b></b>
        <span class="active"><i>03</i>执行</span>
        <b></b>
        <span><i>04</i>复盘</span>
      </div>

      <div class="confirm-instrument">
        <div><strong>{{ stock.name }}</strong><span>{{ stock.symbol }} · {{ stock.industry }}</span></div>
        <em :class="preview.side === 'BUY' ? 'rise' : 'fall'">{{ preview.side === 'BUY' ? '买入' : '卖出' }}</em>
      </div>
      <dl class="confirm-ledger">
        <div><dt>委托方式</dt><dd>{{ preview.order_type === 'MARKET' ? '市价委托' : `限价 ¥ ${formatNumber(preview.limit_price ?? 0)}` }}</dd></div>
        <div><dt>委托数量</dt><dd>{{ preview.quantity.toLocaleString('zh-CN') }} 股</dd></div>
        <div><dt>行情参考价</dt><dd>¥ {{ formatNumber(preview.reference_price) }}</dd></div>
        <div><dt>预估成交金额</dt><dd>¥ {{ formatNumber(preview.estimated_amount) }}</dd></div>
        <div><dt>费用</dt><dd>¥ {{ formatNumber(preview.estimated_fee) }}</dd></div>
        <div><dt>成交后总仓位</dt><dd>{{ formatNumber(preview.post_position_ratio) }}%</dd></div>
      </dl>
      <div v-if="preview.warnings.length" class="confirm-warning">
        <strong>提交前提示</strong>
        <span v-for="warning in preview.warnings" :key="warning">{{ warning }}</span>
      </div>
      <p class="confirm-rule">限价委托不保证立即成交；待成交委托可在“委托记录”中撤销。A 股买入持仓遵循 T+1 可卖规则。</p>
      <footer>
        <button class="dialog-secondary" :disabled="submitting" @click="emit('close')">返回修改</button>
        <button class="dialog-primary" :class="preview.side === 'BUY' ? 'buy-button' : 'sell-button'" :disabled="submitting" @click="emit('confirm')">
          {{ submitting ? '正在提交…' : '确认提交委托' }}
        </button>
      </footer>
    </section>
  </div>
</template>
