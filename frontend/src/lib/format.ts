import i18n from '../i18n'

function locale(): string {
  return i18n.language.startsWith('nl') ? 'nl-NL' : 'en-GB'
}

export function fmtEur(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(locale(), {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n)
}

export function fmtPrice(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  // Low-priced assets (typical crypto alts) need extra precision on the trade page.
  const minDigits = n >= 5 ? 2 : 4
  const maxDigits = n >= 100 ? 2 : n >= 5 ? 4 : 8
  return `€ ${n.toLocaleString(locale(), { minimumFractionDigits: minDigits, maximumFractionDigits: maxDigits })}`
}

/** Decimal precision for lightweight-charts axis labels (matches fmtPrice tiers). */
export function chartPriceFormat(referencePrice: number): {
  type: 'price'
  precision: number
  minMove: number
} {
  const precision = referencePrice >= 100 ? 2 : referencePrice >= 1 ? 4 : 8
  const minMove = precision === 2 ? 0.01 : precision === 4 ? 0.0001 : 0.00000001
  return { type: 'price', precision, minMove }
}

export function fmtAmount(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString(locale(), { maximumFractionDigits: 8 })
}

export function fmtPct(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = seconds / 60
  if (minutes < 60) return `${Math.floor(minutes)}m ${Math.round(seconds % 60)}s`
  const hours = minutes / 60
  if (hours < 24) return `${Math.floor(hours)}h ${Math.round(minutes % 60)}m`
  const days = hours / 24
  return `${Math.floor(days)}d ${Math.round(hours % 24)}h`
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString(locale(), {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function stripHtml(html: string): string {
  return html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
}
