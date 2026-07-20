import { useId, useRef } from 'react'
import { useTranslation } from 'react-i18next'

type Props = {
  ticker: string
  className?: string
}

export default function FundInfoButton({ ticker, className = '' }: Props) {
  const { t } = useTranslation()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  const name = t(`funds.${ticker}.name`, { defaultValue: ticker })
  const description = t(`funds.${ticker}.description`, { defaultValue: '' })
  if (!description) return null

  return (
    <>
      <button
        type="button"
        aria-label={t('funds.infoButton', { name })}
        title={t('funds.infoButton', { name })}
        onClick={(e) => {
          e.stopPropagation()
          dialogRef.current?.showModal()
        }}
        className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-slate-600 text-[10px] font-bold leading-none text-slate-400 transition-colors hover:border-amber-500/50 hover:bg-amber-500/10 hover:text-amber-400 ${className}`}
      >
        i
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        className="fixed left-1/2 top-1/2 w-[min(92vw,28rem)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-slate-700 bg-slate-900 p-0 text-slate-100 shadow-xl backdrop:bg-black/60"
        onClick={(e) => {
          if (e.target === dialogRef.current) dialogRef.current.close()
        }}
      >
        <div className="border-b border-slate-800 px-5 py-4">
          <p className="text-xs uppercase tracking-wide text-slate-500">{ticker}-EUR</p>
          <h2 id={titleId} className="mt-1 text-lg font-semibold text-amber-400">
            {name}
          </h2>
        </div>
        <div className="px-5 py-4 text-sm leading-relaxed text-slate-300">{description}</div>
        <div className="flex justify-end border-t border-slate-800 px-5 py-3">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
          >
            {t('funds.close')}
          </button>
        </div>
      </dialog>
    </>
  )
}
