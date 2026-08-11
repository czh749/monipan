<script setup lang="ts">
import type { Order, Position, Trade } from '../types'
import { dateTime, formatNumber, riseClass } from '../utils/formatters'

type TradingTab = 'positions' | 'orders' | 'trades'

defineProps<{
  positions: Position[]
  orders: Order[]
  trades: Trade[]
  activeTab: TradingTab
}>()

const emit = defineEmits<{
  'update:activeTab': [tab: TradingTab]
  chooseForSell: [symbol: string]
  cancelOrder: [orderNo: string]
}>()

function statusLabel(status: string) {
  return ({ FILLED: '已成交', PENDING: '待成交', CANCELED: '已撤单', REJECTED: '已拒绝' } as Record<string, string>)[status] ?? status
}
</script>

<template>
  <section class="panel records-panel">
    <div class="records-head">
      <div><span class="section-kicker">ACCOUNT LEDGER</span><h2>交易记录</h2></div>
      <div class="tabs">
        <button :class="{ active: activeTab === 'positions' }" @click="emit('update:activeTab', 'positions')">当前持仓 <b>{{ positions.length }}</b></button>
        <button :class="{ active: activeTab === 'orders' }" @click="emit('update:activeTab', 'orders')">委托记录 <b>{{ orders.length }}</b></button>
        <button :class="{ active: activeTab === 'trades' }" @click="emit('update:activeTab', 'trades')">成交记录 <b>{{ trades.length }}</b></button>
      </div>
    </div>
    <div class="table-wrap records-table-wrap">
      <table v-if="activeTab === 'positions'">
        <thead><tr><th>股票</th><th>持仓数量</th><th>T+1 可卖</th><th>成本价</th><th>现价</th><th>市值</th><th>持仓盈亏</th><th>收益率</th><th></th></tr></thead>
        <tbody>
          <tr v-for="position in positions" :key="position.symbol">
            <td><strong>{{ position.stock_name }}</strong><small>{{ position.symbol }}</small></td>
            <td>{{ position.quantity.toLocaleString() }}</td>
            <td>{{ position.sellable_quantity.toLocaleString() }}</td>
            <td>{{ formatNumber(position.average_cost, 4) }}</td>
            <td>{{ formatNumber(position.current_price) }}</td>
            <td>¥ {{ formatNumber(position.market_value) }}</td>
            <td :class="riseClass(position.profit_loss)">{{ Number(position.profit_loss) >= 0 ? '+' : '' }}{{ formatNumber(position.profit_loss) }}</td>
            <td :class="riseClass(position.profit_loss_percent)">{{ formatNumber(position.profit_loss_percent) }}%</td>
            <td><button class="text-action" :disabled="!position.sellable_quantity" @click="emit('chooseForSell', position.symbol)">卖出</button></td>
          </tr>
        </tbody>
      </table>
      <table v-else-if="activeTab === 'orders'">
        <thead><tr><th>时间</th><th>股票</th><th>方向</th><th>类型 / 委托价</th><th>委托数量</th><th>成交价格</th><th>费用</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="order in orders" :key="order.order_no">
            <td>{{ dateTime(order.created_at) }}</td>
            <td><strong>{{ order.stock_name }}</strong><small>{{ order.symbol }}</small></td>
            <td :class="order.side === 'BUY' ? 'rise' : 'fall'">{{ order.side === 'BUY' ? '买入' : '卖出' }}</td>
            <td>{{ order.order_type === 'LIMIT' ? `限价 ${formatNumber(order.limit_price ?? order.price)}` : '市价' }}</td>
            <td>{{ order.quantity }}</td><td>{{ order.status === 'FILLED' ? formatNumber(order.price) : '—' }}</td>
            <td>{{ formatNumber(order.fee) }}</td><td><span class="status-pill" :class="`status-${order.status.toLowerCase()}`">{{ statusLabel(order.status) }}</span></td>
            <td><button v-if="order.cancelable" class="text-action cancel-action" @click="emit('cancelOrder', order.order_no)">撤单</button><small v-else class="order-id">{{ order.order_no }}</small></td>
          </tr>
        </tbody>
      </table>
      <table v-else>
        <thead><tr><th>成交时间</th><th>股票</th><th>方向</th><th>数量</th><th>成交价</th><th>成交金额</th><th>费用</th><th>成交编号</th></tr></thead>
        <tbody>
          <tr v-for="trade in trades" :key="trade.trade_no">
            <td>{{ dateTime(trade.created_at) }}</td>
            <td><strong>{{ trade.stock_name }}</strong><small>{{ trade.symbol }}</small></td>
            <td :class="trade.side === 'BUY' ? 'rise' : 'fall'">{{ trade.side === 'BUY' ? '买入' : '卖出' }}</td>
            <td>{{ trade.quantity }}</td><td>{{ formatNumber(trade.price) }}</td>
            <td>¥ {{ formatNumber(trade.amount) }}</td><td>{{ formatNumber(trade.fee) }}</td>
            <td class="muted">{{ trade.trade_no }}</td>
          </tr>
        </tbody>
      </table>
      <div
        v-if="(activeTab === 'positions' && !positions.length) || (activeTab === 'orders' && !orders.length) || (activeTab === 'trades' && !trades.length)"
        class="empty-state"
      >
        <span>◎</span><strong>暂无数据</strong>
        <small>从上方行情列表选择股票，提交第一笔模拟委托吧</small>
      </div>
    </div>
  </section>
</template>
