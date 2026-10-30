import assert from 'node:assert/strict'
import { after, before, beforeEach, afterEach, test } from 'node:test'
import { createRenderer, nextTick, ref } from 'vue'
import { createServer } from 'vite'
import { gsap } from 'gsap'

let server
let useOrderEntry
let useTradingScene
let api
let useSecuritySearch
let useWatchlist
let useMarketData
let useStockHistory
let timers
let mounted
const originalGlobals = ['window', 'document'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)])

const renderer = createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
})

function mountComposable(setup) {
  let result
  const app = renderer.createApp({ setup() { result = setup(); return () => null } })
  app.mount({})
  mounted.push(app)
  return result
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function preview(overrides = {}) {
  return {
    symbol: '600519', side: 'BUY', quantity: 100, order_type: 'MARKET',
    allowed: true, limit_price: null, ...overrides,
  }
}

function workspace(overrides = {}) {
  const options = {
    selectedSymbol: ref('600519'),
    selectedStock: ref({ symbol: '600519', name: '贵州茅台', price: '1500' }),
    currentUser: ref({ id: 1, username: 'student' }),
    currentTime: ref(0), active: ref(true),
    account: ref(null), positions: ref([]), orders: ref([]),
    placeOrder: async () => null, cancelOrder: async () => {},
    ...overrides,
  }
  const entry = mountComposable(() => useOrderEntry(options))
  return { options, entry }
}

function runTimers(delay) {
  for (const [id, timer] of [...timers]) {
    if (timer.delay === delay) { timers.delete(id); timer.callback() }
  }
}

before(async () => {
  server = await createServer({
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom', optimizeDeps: { noDiscovery: true },
  })
  ;({ useOrderEntry } = await server.ssrLoadModule('/src/composables/useOrderEntry.ts'))
  ;({ useTradingScene } = await server.ssrLoadModule('/src/composables/useTradingScene.ts'))
  ;({ api } = await server.ssrLoadModule('/src/api.ts'))
  ;({ useSecuritySearch } = await server.ssrLoadModule('/src/composables/useSecuritySearch.ts'))
  ;({ useWatchlist } = await server.ssrLoadModule('/src/composables/useWatchlist.ts'))
  ;({ useMarketData } = await server.ssrLoadModule('/src/composables/useMarketData.ts'))
  ;({ useStockHistory } = await server.ssrLoadModule('/src/composables/useStockHistory.ts'))
})

after(async () => { await server?.close() })

beforeEach((context) => {
  timers = new Map()
  mounted = []
  let nextTimerId = 0
  globalThis.window = {
    setTimeout(callback, delay) {
      const id = ++nextTimerId
      timers.set(id, { callback, delay })
      return id
    },
    clearTimeout(id) { timers.delete(id) },
  }
  globalThis.document = { visibilityState: 'visible' }
  context.mock.method(api, 'previewOrder', async (payload) => preview(payload))
})

afterEach(() => {
  for (const app of mounted) app.unmount()
  for (const [key, descriptor] of originalGlobals) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor)
    else delete globalThis[key]
  }
})

test('research-only and pending stocks never request an order preview', async (context) => {
  let calls = 0
  context.mock.method(api, 'previewOrder', async () => { calls++; return preview() })
  const { entry, options } = workspace({ selectedStock: ref({ symbol: '688900', research_only: true }) })
  assert.equal(await entry.refreshOrderPreview(), null)
  options.selectedStock.value = { symbol: '600519', quote_status: 'pending' }
  assert.equal(await entry.refreshOrderPreview(), null)
  assert.equal(calls, 0)
})

test('history polling reads only cache and unmount cancels the next poll', async (context) => {
  const calls = []
  context.mock.method(api, 'stockHistory', async (symbol, refresh) => {
    calls.push([symbol, refresh])
    return { symbol, complete: false, bars: [], preparation_state: 'failed' }
  })
  const state = mountComposable(() => useStockHistory({ isOpen: () => true, currentSymbol: () => '688900' }))
  await state.loadHistory('688900')
  runTimers(30_000)
  await Promise.resolve(); await Promise.resolve()
  assert.deepEqual(calls, [['688900', true], ['688900', false]])
  mounted[0].unmount()
  runTimers(30_000)
  assert.equal(calls.length, 2)
  assert.equal(state.history.value, null)
})

test('switching stocks discards late history and a retry failure retains the cache', async (context) => {
  const old = deferred()
  context.mock.method(api, 'stockHistory', () => old.promise)
  let symbol = '688900'
  const state = mountComposable(() => useStockHistory({ isOpen: () => true, currentSymbol: () => symbol }))
  const pending = state.loadHistory(symbol)
  state.resetHistory()
  symbol = '688901'
  context.mock.method(api, 'stockHistory', async () => ({ symbol, bars: [{ close_price: '10' }] }))
  await state.loadHistory(symbol)
  old.resolve({ symbol: '688900', bars: [] })
  await pending
  assert.equal(state.history.value.symbol, '688901')
  context.mock.method(api, 'stockHistory', async () => { throw new Error('offline') })
  await state.loadHistory(symbol)
  assert.equal(state.history.value.bars.length, 1)
  assert.equal(state.error.value, 'offline')
})

test('directory search invalidates old responses immediately and debounces input', async (context) => {
  const old = deferred()
  const current = deferred()
  context.mock.method(api, 'searchSecurities', () => old.promise)
  const search = mountComposable(useSecuritySearch)
  const loadingOld = search.refresh()
  search.keyword.value = '688900'
  old.resolve({ items: [{ symbol: 'old' }] })
  await loadingOld
  assert.equal(search.result.value, null)
  assert.equal(search.loading.value, true)
  context.mock.method(api, 'searchSecurities', () => current.promise)
  runTimers(300)
  current.resolve({ items: [{ symbol: '688900' }], quota: { used: 1, limit: 30 } })
  await Promise.resolve(); await Promise.resolve()
  assert.equal(search.result.value.items[0].symbol, '688900')
  assert.equal(search.loading.value, false)
})

test('search error can retry and unmount discards a pending response', async (context) => {
  context.mock.method(api, 'searchSecurities', async () => { throw new Error('offline') })
  const search = mountComposable(useSecuritySearch)
  await search.refresh()
  assert.equal(search.error.value, 'offline')
  const pending = deferred()
  context.mock.method(api, 'searchSecurities', () => pending.promise)
  const request = search.refresh()
  mounted[0].unmount()
  pending.resolve({ items: [{ symbol: 'old' }] })
  await request
  assert.equal(search.result.value, null)
})

test('watchlist reset rejects stale account results and coalesces duplicate clicks', async (context) => {
  const pending = deferred()
  let calls = 0
  context.mock.method(api, 'addWatchlist', () => { calls++; return pending.promise })
  const list = mountComposable(useWatchlist)
  const a = list.toggleWatchlist('688900')
  const b = list.toggleWatchlist('688900')
  assert.equal(a, b)
  list.resetWatchlist()
  pending.resolve({ symbol: '688900' })
  await a
  assert.equal(calls, 1)
  assert.deepEqual(list.symbols.value, [])
})

test('stopped market refresh cannot publish another account stocks', async (context) => {
  const pending = deferred()
  context.mock.method(api, 'stocks', () => pending.promise)
  context.mock.method(api, 'marketStatus', async () => null)
  context.mock.method(api, 'marketIndices', async () => [])
  globalThis.document.removeEventListener = () => {}
  const data = mountComposable(useMarketData)
  const old = data.refreshMarket()
  data.stopMarketPolling()
  context.mock.method(api, 'stocks', async () => [{ symbol: 'new' }])
  await data.refreshMarket()
  pending.resolve([{ symbol: 'private-old' }])
  await old
  assert.deepEqual(data.stocks.value.map(s => s.symbol), ['new'])
})

test('only the latest preview response can update the order ticket', async (context) => {
  const first = deferred()
  const second = deferred()
  context.mock.method(api, 'previewOrder', () => first.promise)
  const { entry } = workspace()
  const firstRequest = entry.refreshOrderPreview()
  context.mock.method(api, 'previewOrder', () => second.promise)
  entry.quantity.value = 200
  const secondRequest = entry.refreshOrderPreview()
  second.resolve(preview({ quantity: 200 }))
  await secondRequest
  first.resolve(preview())
  assert.equal(await firstRequest, null)
  assert.equal(entry.orderPreview.value.quantity, 200)
  assert.equal(entry.previewLoading.value, false)
})

test('invalid lots and nonpositive limit prices do not request a preview', async () => {
  const { entry } = workspace()
  for (const quantity of [0, -100, 150]) {
    entry.quantity.value = quantity
    assert.equal(await entry.refreshOrderPreview(), null)
  }
  entry.quantity.value = 100
  entry.orderType.value = 'LIMIT'
  entry.limitPrice.value = 0
  assert.equal(await entry.refreshOrderPreview(), null)
  assert.equal(api.previewOrder.mock.callCount(), 0)
})

test('server risk rejection keeps the confirmation closed', async (context) => {
  context.mock.method(api, 'previewOrder', async () => preview({
    allowed: false, blocking_reason: '可用资金不足',
  }))
  const { entry } = workspace()
  await entry.reviewOrder()
  assert.equal(entry.confirmationOpen.value, false)
  assert.equal(entry.orderNotice.value.message, '可用资金不足')
})

test('changing order terms after review requires another confirmation', async (context) => {
  const submit = context.mock.fn()
  const { entry } = workspace({ placeOrder: submit })
  entry.orderType.value = 'LIMIT'
  await entry.reviewOrder()
  assert.equal(entry.confirmationOpen.value, true)
  entry.limitPrice.value = 1499
  await entry.confirmOrder()
  assert.equal(entry.confirmationOpen.value, false)
  assert.equal(entry.orderNotice.value.title, '请重新复核委托')
  assert.equal(entry.orderPreview.value.limit_price, 1499)
  assert.equal(submit.mock.callCount(), 0)
})

test('market and limit orders preserve payloads and result notices', async (context) => {
  for (const orderType of ['MARKET', 'LIMIT']) {
    const submit = context.mock.fn(async () => ({
      status: orderType === 'LIMIT' ? 'PENDING' : 'FILLED',
      order_no: 'ORDER-1', price: '1500', limit_price: '1500',
    }))
    const { entry: subject } = workspace({ placeOrder: submit })
    subject.orderType.value = orderType
    await subject.reviewOrder()
    await subject.confirmOrder()
    assert.deepEqual(submit.mock.calls[0].arguments[0], {
      symbol: '600519', side: 'BUY', quantity: 100, order_type: orderType,
      ...(orderType === 'LIMIT' ? { limit_price: 1500 } : {}),
    })
    assert.equal(subject.confirmationOpen.value, false)
    assert.equal(subject.orderNotice.value.kind, 'success')
    assert.equal(subject.orderNotice.value.title,
      orderType === 'LIMIT' ? '限价委托已进入队列' : '买入已成交')
  }
})

test('logout invalidates an in-flight preview and clears private order UI', async (context) => {
  const pending = deferred()
  context.mock.method(api, 'previewOrder', () => pending.promise)
  const { options, entry } = workspace()
  const request = entry.refreshOrderPreview()
  entry.confirmationOpen.value = true
  options.currentUser.value = null
  assert.equal(entry.confirmationOpen.value, false)
  assert.equal(entry.previewLoading.value, false)
  pending.resolve(preview())
  assert.equal(await request, null)
  assert.equal(entry.orderPreview.value, null)
  assert.equal(entry.orderNotice.value, null)
})

test('rapid edits share one preview timer and unmount clears pending work', async () => {
  const { entry } = workspace()
  entry.quantity.value = 200
  await nextTick()
  entry.quantity.value = 300
  await nextTick()
  assert.equal(timers.size, 1)
  runTimers(180)
  await nextTick()
  assert.equal(api.previewOrder.mock.callCount(), 1)
  assert.equal(entry.orderPreview.value.quantity, 300)
  entry.quantity.value = 400
  await nextTick()
  mounted[0].unmount()
  assert.equal(timers.size, 0)
  runTimers(180)
  assert.equal(api.previewOrder.mock.callCount(), 1)
})

test('unmount invalidates a pending review without showing a notice', async (context) => {
  const pending = deferred()
  context.mock.method(api, 'previewOrder', () => pending.promise)
  const { entry } = workspace()
  const review = entry.reviewOrder()
  mounted[0].unmount()
  pending.resolve(preview())
  await review
  assert.equal(entry.orderPreview.value, null)
  assert.equal(entry.orderNotice.value, null)
  assert.equal(entry.confirmationOpen.value, false)
  assert.equal(timers.size, 0)
})

test('a completed submission cannot show notices in another session', async (context) => {
  const pending = deferred()
  const submit = context.mock.fn(() => pending.promise)
  const { options, entry } = workspace({ placeOrder: submit })
  await entry.reviewOrder()
  const confirmation = entry.confirmOrder()
  options.currentUser.value = { id: 2, username: 'another-user' }
  pending.resolve({ status: 'FILLED', order_no: 'OLD-ORDER', price: '1500' })
  await confirmation
  assert.equal(entry.orderNotice.value, null)
  assert.equal(entry.confirmationOpen.value, false)
  assert.equal(api.previewOrder.mock.callCount(), 1)
})

test('canceling an order refreshes the preview and reports the result', async (context) => {
  const cancel = context.mock.fn(async () => {})
  const { entry } = workspace({ cancelOrder: cancel })
  await entry.handleCancelOrder('ORDER-1')
  assert.equal(cancel.mock.calls[0].arguments[0], 'ORDER-1')
  assert.equal(api.previewOrder.mock.callCount(), 1)
  assert.equal(entry.orderNotice.value.title, '委托已撤销')
})

test('periodic previews only refresh a visible active trading desk', async () => {
  const { options } = workspace()
  options.currentTime.value = 30_000
  await nextTick()
  assert.equal(api.previewOrder.mock.callCount(), 1)
  options.active.value = false
  options.currentTime.value = 60_000
  await nextTick()
  options.active.value = true
  document.visibilityState = 'hidden'
  options.currentTime.value = 90_000
  await nextTick()
  assert.equal(api.previewOrder.mock.callCount(), 1)
})

test('leaving the trading scene reverts animations and unmount cleans them up', async (context) => {
  let reverts = 0
  const scopes = []
  const listeners = new Set()
  window.addEventListener = (_event, listener) => listeners.add(listener)
  window.removeEventListener = (_event, listener) => listeners.delete(listener)
  context.mock.method(gsap, 'timeline', () => ({ fromTo() { return this } }))
  context.mock.method(gsap, 'quickTo', () => () => {})
  context.mock.method(gsap, 'matchMedia', () => ({
    cleanup: null,
    add(_queries, callback, scope) {
      scopes.push(scope)
      this.cleanup = callback({ conditions: { desktop: true, reduceMotion: false } })
    },
    revert() { reverts++; this.cleanup?.() },
  }))
  const active = ref(true)
  const scene = mountComposable(() => useTradingScene(active))
  scene.appShellRoot.value = { querySelectorAll: () => [] }
  scene.tradingBackdrop.value = {}
  scene.tradingGlow.value = {}
  await scene.animateTradingStage()
  assert.equal(scopes[0], scene.appShellRoot.value)
  assert.equal(listeners.size, 1)
  active.value = false
  assert.equal(reverts, 1)
  assert.equal(listeners.size, 0)
  await scene.animateTradingStage()
  assert.equal(scopes.length, 1)
  active.value = true
  await scene.animateTradingStage()
  mounted[0].unmount()
  assert.equal(reverts, 2)
  assert.equal(listeners.size, 0)
})
