import { useTranslation } from 'react-i18next'

export default function LanguageSwitcher() {
  const { i18n } = useTranslation()
  const current = i18n.language.startsWith('nl') ? 'nl' : 'en'

  return (
    <span className="flex overflow-hidden rounded-md border border-slate-700">
      {(['nl', 'en'] as const).map((lng) => (
        <button
          key={lng}
          type="button"
          onClick={() => i18n.changeLanguage(lng)}
          className={`px-2 py-1 text-xs font-semibold uppercase transition-colors ${
            current === lng ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'
          }`}
        >
          {lng}
        </button>
      ))}
    </span>
  )
}
