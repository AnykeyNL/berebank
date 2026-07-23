import { useTranslation } from 'react-i18next'
import type { AssetClass } from '../lib/types'

/**
 * Small inline icon indicating an asset's class: a coin for crypto, a bar
 * chart for stocks, a pie chart for funds and a stack of ingots for
 * commodities.
 */
export default function AssetClassIcon({ assetClass, className = 'h-4 w-4' }: { assetClass: AssetClass; className?: string }) {
  const { t } = useTranslation()
  const label = t(`assetClass.${assetClass}`)

  if (assetClass === 'stock') {
    return (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
        className={`${className} text-sky-400`} role="img" aria-label={label}>
        <title>{label}</title>
        <path d="M2.5 13.5v-4" />
        <path d="M6.5 13.5v-7" />
        <path d="M10.5 13.5v-5" />
        <path d="M14 13.5v-9" />
      </svg>
    )
  }
  if (assetClass === 'commodity') {
    return (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"
        className={`${className} text-orange-400`} role="img" aria-label={label}>
        <title>{label}</title>
        <path d="M6.2 3.5h3.6l1.2 3.5H5l1.2-3.5Z" />
        <path d="M2.7 9h3.6l1.2 3.5H1.5L2.7 9Z" />
        <path d="M9.7 9h3.6l1.2 3.5H8.5L9.7 9Z" />
      </svg>
    )
  }
  if (assetClass === 'fund') {
    return (
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
        className={`${className} text-violet-400`} role="img" aria-label={label}>
        <title>{label}</title>
        <path d="M8 2.5a5.5 5.5 0 1 0 5.5 5.5H8V2.5Z" />
        <path d="M10.5 1.8a5.5 5.5 0 0 1 3.7 3.7h-3.7V1.8Z" fill="currentColor" stroke="none" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
      className={`${className} text-amber-400`} role="img" aria-label={label}>
      <title>{label}</title>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.8v6.4M10 6.2c-.5-.9-3.9-1.2-3.9.6 0 1.9 3.8 1 3.8 2.9 0 1.7-3.3 1.5-3.9.5" strokeLinecap="round" />
    </svg>
  )
}
