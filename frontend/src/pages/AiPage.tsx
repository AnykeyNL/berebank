import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

const cardClass = 'rounded-xl border border-slate-800 bg-slate-900/60 p-6'

function StepList({ keys }: { keys: string[] }) {
  const { t } = useTranslation()
  return (
    <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-300">
      {keys.map((k) => (
        <li key={k}>{t(k)}</li>
      ))}
    </ol>
  )
}

function ServerUrl() {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const mcpUrl = `${window.location.origin}/mcp`

  async function copyUrl() {
    await navigator.clipboard.writeText(mcpUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-slate-300">{t('aiPage.serverUrl')}</span>
      <div className="flex max-w-md gap-2">
        <input
          type="text"
          readOnly
          value={mcpUrl}
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm outline-none focus:border-amber-500"
        />
        <button
          type="button"
          onClick={copyUrl}
          className="shrink-0 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-amber-500 hover:text-amber-400"
        >
          {copied ? t('mcp.copied') : t('mcp.copy')}
        </button>
      </div>
    </div>
  )
}

export default function AiPage() {
  const { t } = useTranslation()

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-bold text-white">{t('aiPage.title')}</h1>

      <section className={cardClass}>
        <p className="mb-4 text-sm leading-relaxed text-slate-300">{t('aiPage.intro1')}</p>
        <p className="mb-6 text-sm leading-relaxed text-slate-400">{t('aiPage.intro2')}</p>
        <ServerUrl />
      </section>

      <section className={cardClass}>
        <h2 className="mb-3 text-lg font-semibold text-white">{t('aiPage.whatTitle')}</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-slate-300">
          <li>{t('aiPage.whatRead')}</li>
          <li>{t('aiPage.whatTrade')}</li>
        </ul>
        <Link
          to="/profile"
          className="mt-4 inline-block text-sm font-medium text-amber-400 hover:text-amber-300"
        >
          {t('aiPage.profileLink')} →
        </Link>
      </section>

      <section className={cardClass}>
        <h2 className="mb-1 text-lg font-semibold text-white">{t('aiPage.claudeTitle')}</h2>
        <p className="mb-4 text-sm text-slate-400">{t('aiPage.claudeIntro')}</p>
        <StepList
          keys={[1, 2, 3, 4, 5].map((n) => `aiPage.claudeStep${n}`)}
        />
      </section>

      <section className={cardClass}>
        <h2 className="mb-1 text-lg font-semibold text-white">{t('aiPage.chatgptTitle')}</h2>
        <p className="mb-4 text-sm text-slate-400">{t('aiPage.chatgptIntro')}</p>
        <StepList
          keys={[1, 2, 3, 4, 5, 6].map((n) => `aiPage.chatgptStep${n}`)}
        />
      </section>

      <section className={cardClass}>
        <h2 className="mb-3 text-lg font-semibold text-white">{t('aiPage.examplesTitle')}</h2>
        <ul className="list-disc space-y-2 pl-5 text-sm text-slate-300">
          <li>{t('aiPage.example1')}</li>
          <li>{t('aiPage.example2')}</li>
          <li>{t('aiPage.example3')}</li>
          <li>{t('aiPage.example4')}</li>
        </ul>
      </section>

      <p className="text-xs leading-relaxed text-slate-500">{t('aiPage.disclaimer')}</p>
    </div>
  )
}
