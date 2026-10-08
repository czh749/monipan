export function formatNumber(value: string | number, digits = 2) {
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function compactVolume(value: number) {
  if (value >= 100_000_000) return `${formatNumber(value / 100_000_000)}亿`
  if (value >= 10_000) return `${formatNumber(value / 10_000)}万`
  return value.toLocaleString('zh-CN')
}

export function compactTurnover(value: string | number) {
  const amount = Number(value)
  if (amount >= 1_000_000_000_000) {
    return `${formatNumber(amount / 1_000_000_000_000)} 万亿`
  }
  if (amount >= 100_000_000) return `${formatNumber(amount / 100_000_000)} 亿`
  if (amount >= 10_000) return `${formatNumber(amount / 10_000)} 万`
  return formatNumber(amount)
}

export function quoteNumber(value: string | number) {
  return Number(value) > 0 ? formatNumber(value) : '—'
}

export function utcDate(value: string | null | undefined) {
  if (!value) return null
  const hasTimeZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
  return new Date(hasTimeZone ? value : `${value}Z`)
}

export function dateTime(value: string | null | undefined) {
  const date = utcDate(value)
  if (!date) return '—'
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function fullDateTime(value: string | null | undefined) {
  const date = utcDate(value)
  if (!date) return '尚未更新'
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function shortDateTime(value: string | null | undefined) {
  const date = utcDate(value)
  if (!date) return '—'
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function relativeTime(
  value: string | null | undefined,
  currentTime = Date.now(),
) {
  const date = utcDate(value)
  if (!date) return '暂无成功记录'
  const seconds = Math.max(0, Math.floor((currentTime - date.getTime()) / 1000))
  if (seconds < 10) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  return `${Math.floor(seconds / 86400)} 天前`
}

export function riseClass(value: string | number) {
  const parsed = Number(value)
  if (parsed > 0) return 'rise'
  if (parsed < 0) return 'fall'
  return 'flat'
}
