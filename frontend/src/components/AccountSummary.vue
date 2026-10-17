<script setup lang="ts">
import type { Account } from '../types'
import { formatNumber, riseClass } from '../utils/formatters'

defineProps<{
  account: Account
  username: string
  positionCount: number
  positionRatio: number
}>()
</script>

<template>
  <section class="asset-cockpit account-asset-cockpit">
    <article class="equity-console">
      <div class="console-heading">
        <div><span class="section-kicker">TOTAL EQUITY</span><h2>账户净值</h2></div>
        <span class="console-code">CNY / {{ username.toUpperCase() }}</span>
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
        <span class="console-code">{{ positionCount }} POSITIONS</span>
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

  <section class="mobile-capital">
    <article>
      <span class="card-label">可用资金</span>
        <strong>¥ {{ formatNumber(account.available_cash) }}</strong>
    </article>
    <article>
      <span class="card-label">持仓市值</span>
        <strong>¥ {{ formatNumber(account.market_value) }}</strong>
    </article>
  </section>
</template>
