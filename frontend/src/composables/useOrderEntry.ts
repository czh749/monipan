import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { api } from '../api'
import type { CurrentUser, OrderPreview, Stock } from '../types'
import type { useTrading } from './useTrading'
import { formatNumber } from '../utils/formatters'

export interface OrderNotice {
  kind: 'success' | 'error'
  title: string
  message: string
  meta: string
}

type OrderEntryOptions = Pick<ReturnType<typeof useTrading>,
  'account' | 'positions' | 'orders' | 'placeOrder' | 'cancelOrder'
> & {
  selectedSymbol: Readonly<Ref<string>>
  selectedStock: Readonly<Ref<Stock | undefined>>
  currentUser: Readonly<Ref<CurrentUser | null>>
  currentTime: Readonly<Ref<number>>
  active: Readonly<Ref<boolean>>
}

export function useOrderEntry({
  selectedSymbol, selectedStock, currentUser, currentTime, active,
  account, positions, orders, placeOrder, cancelOrder,
}: OrderEntryOptions) {
  const side = ref<'BUY' | 'SELL'>('BUY')
  const orderType = ref<'MARKET' | 'LIMIT'>('MARKET')
  const limitPrice = ref(0)
  const quantity = ref(100)
  const orderNotice = ref<OrderNotice | null>(null)
  const orderPreview = ref<OrderPreview | null>(null)
  const previewLoading = ref(false)
  const confirmationOpen = ref(false)
  let orderNoticeTimer: number | undefined
  let previewTimer: number | undefined
  let previewRequestId = 0
  let disposed = false

  function isCurrentSession(user: CurrentUser | null) {
    return !disposed && user !== null && currentUser.value === user
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

  async function refreshOrderPreview() {
    const stock = selectedStock.value
    if (disposed || !currentUser.value || !stock || stock.research_only || stock.quote_status === 'pending' || quantity.value <= 0 || quantity.value % 100 !== 0 || (orderType.value === 'LIMIT' && limitPrice.value <= 0)) {
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
    const user = currentUser.value
    const stock = selectedStock.value
    if (!isCurrentSession(user)) return
    if (!stock || quantity.value <= 0 || quantity.value % 100 !== 0) {
      showOrderNotice({
        kind: 'error',
        title: '委托未提交',
        message: '委托数量必须是 100 股的整数倍',
        meta: stock
          ? `${side.value === 'BUY' ? '买入' : '卖出'} ${stock.name}`
          : '请选择有效股票',
      })
      return
    }

    const latestPreview = await refreshOrderPreview()
    if (!isCurrentSession(user)) return
    if (!latestPreview?.allowed || !previewMatchesSelection(latestPreview)) {
      showOrderNotice({
        kind: 'error',
        title: '委托未通过校验',
        message: latestPreview?.blocking_reason ?? '请等待风险预览完成',
        meta: stock.symbol,
      })
      return
    }
    confirmationOpen.value = true
  }

  async function confirmOrder() {
    const user = currentUser.value
    if (!isCurrentSession(user) || !selectedStock.value || !orderPreview.value) return
    if (!orderPreview.value.allowed || !previewMatchesSelection(orderPreview.value)) {
      confirmationOpen.value = false
      await refreshOrderPreview()
      if (!isCurrentSession(user)) return
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
      if (!order || !isCurrentSession(user)) return
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
      if (!isCurrentSession(user)) return
      showOrderNotice({
        kind: 'error',
        title: `${orderSide === 'BUY' ? '买入' : '卖出'}失败`,
        message: reason instanceof Error ? reason.message : '系统未能完成委托，请稍后重试',
        meta: `${stock.symbol} ${stock.name} · ${orderQuantity.toLocaleString('zh-CN')} 股`,
      })
    }
  }

  async function handleCancelOrder(orderNo: string) {
    const user = currentUser.value
    if (!isCurrentSession(user)) return
    try {
      await cancelOrder(orderNo)
      if (!isCurrentSession(user)) return
      showOrderNotice({ kind: 'success', title: '委托已撤销', message: '待成交委托已从撮合队列移除', meta: orderNo })
      await refreshOrderPreview()
    } catch (reason) {
      if (!isCurrentSession(user)) return
      showOrderNotice({ kind: 'error', title: '撤单失败', message: reason instanceof Error ? reason.message : '无法撤销委托', meta: orderNo })
    }
  }

  function resetOrderEntry() {
    previewRequestId++
    window.clearTimeout(previewTimer)
    closeOrderNotice()
    orderPreview.value = null
    previewLoading.value = false
    confirmationOpen.value = false
  }

  watch(selectedStock, (stock, previous) => {
    if (stock && stock.symbol !== previous?.symbol) limitPrice.value = Number(stock.price)
  }, { immediate: true })

  watch([selectedStock, selectedSymbol, side, quantity, orderType, limitPrice, account, positions, orders], () => {
    window.clearTimeout(previewTimer)
    if (!currentUser.value || disposed) return
    previewTimer = window.setTimeout(() => void refreshOrderPreview(), 180)
  })

  watch(currentUser, resetOrderEntry, { flush: 'sync' })

  watch(() => Math.floor(currentTime.value / 30_000), () => {
    if (currentUser.value && active.value && document.visibilityState === 'visible') {
      void refreshOrderPreview()
    }
  })

  onBeforeUnmount(() => {
    disposed = true
    resetOrderEntry()
  })

  return {
    side, orderType, limitPrice, quantity,
    orderNotice, orderPreview, previewLoading, confirmationOpen,
    closeOrderNotice, refreshOrderPreview, reviewOrder, confirmOrder, handleCancelOrder,
  }
}
