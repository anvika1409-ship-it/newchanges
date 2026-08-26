/**
 * Shared formatting helpers. All money, number, and date/time display in the
 * dashboard must go through these functions rather than ad hoc string
 * concatenation, per the enterprise UI conventions this project follows.
 */

const DEFAULT_LOCALE = 'en-IN'

export function formatCurrency(amount: number, currency = 'INR'): string {
  try {
    return new Intl.NumberFormat(DEFAULT_LOCALE, {
      style: 'currency',
      currency,
      maximumFractionDigits: amount >= 1000 ? 0 : 2,
    }).format(amount)
  } catch {
    return `${currency} ${amount.toFixed(2)}`
  }
}

/** Compact form for KPI tiles, e.g. ₹12.4L / ₹1.2Cr for INR, $12.4K for others. */
export function formatCompactCurrency(amount: number, currency = 'INR'): string {
  const abs = Math.abs(amount)
  if (currency === 'INR') {
    const symbol = '\u20B9'
    if (abs >= 1_00_00_000) return `${symbol}${(amount / 1_00_00_000).toFixed(2)}Cr`
    if (abs >= 1_00_000) return `${symbol}${(amount / 1_00_000).toFixed(2)}L`
    if (abs >= 1_000) return `${symbol}${(amount / 1_000).toFixed(1)}K`
    return formatCurrency(amount, currency)
  }
  try {
    return new Intl.NumberFormat(DEFAULT_LOCALE, {
      style: 'currency',
      currency,
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(amount)
  } catch {
    return formatCurrency(amount, currency)
  }
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(DEFAULT_LOCALE).format(value)
}

export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat(DEFAULT_LOCALE, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function formatPercent(value: number, fractionDigits = 1): string {
  return `${value.toFixed(fractionDigits)}%`
}

export function formatSignedPercent(value: number, fractionDigits = 1): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(fractionDigits)}%`
}

export function formatDateTime(iso: string, timeZone = 'Asia/Kolkata'): string {
  try {
    return new Intl.DateTimeFormat(DEFAULT_LOCALE, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

export function formatDate(iso: string, timeZone = 'Asia/Kolkata'): string {
  try {
    return new Intl.DateTimeFormat(DEFAULT_LOCALE, {
      dateStyle: 'medium',
      timeZone,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

export function formatShortDate(iso: string, timeZone = 'Asia/Kolkata'): string {
  try {
    return new Intl.DateTimeFormat(DEFAULT_LOCALE, {
      month: 'short',
      day: 'numeric',
      timeZone,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

export function formatRelativeTime(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now()
  const diffMinutes = Math.round(diffMs / 60_000)
  const rtf = new Intl.RelativeTimeFormat(DEFAULT_LOCALE, { numeric: 'auto' })

  const absMinutes = Math.abs(diffMinutes)
  if (absMinutes < 60) return rtf.format(diffMinutes, 'minute')

  const diffHours = Math.round(diffMinutes / 60)
  if (Math.abs(diffHours) < 24) return rtf.format(diffHours, 'hour')

  const diffDays = Math.round(diffHours / 24)
  return rtf.format(diffDays, 'day')
}
