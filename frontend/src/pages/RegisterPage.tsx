import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import LanguageSwitcher from '../components/LanguageSwitcher'

const inputClass =
  'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-500'
const labelClass = 'mb-1 block text-sm font-medium text-slate-300'

const WHATSAPP_PATTERN = /^\+[1-9][0-9]{7,14}$/

export default function RegisterPage() {
  const { t } = useTranslation()
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [whatsapp, setWhatsapp] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const cleanedWhatsapp = whatsapp.replace(/[\s\-().]/g, '').replace(/^00/, '+')
    if (!WHATSAPP_PATTERN.test(cleanedWhatsapp)) {
      setError(t('register.whatsappInvalid'))
      return
    }
    if (password.length < 6) {
      setError(t('password.tooShort'))
      return
    }
    if (password !== confirmPassword) {
      setError(t('password.mismatch'))
      return
    }
    setBusy(true)
    try {
      await api('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          display_name: displayName.trim(),
          email,
          whatsapp_number: cleanedWhatsapp,
          password,
        }),
      })
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('register.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4">
      <div className="absolute right-4 top-4">
        <LanguageSwitcher />
      </div>
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="text-5xl">🐻</div>
          <h1 className="mt-3 text-3xl font-bold tracking-tight text-amber-400">de BereBank</h1>
          <p className="mt-1 text-sm text-slate-400">{t('login.tagline')}</p>
        </div>
        {submitted ? (
          <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 text-center shadow-xl">
            <div className="text-3xl">✅</div>
            <h2 className="text-lg font-semibold text-white">{t('register.submittedTitle')}</h2>
            <p className="text-sm text-slate-400">{t('register.submittedNote')}</p>
            <Link
              to="/login"
              className="inline-block rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-amber-400"
            >
              {t('register.backToLogin')}
            </Link>
          </div>
        ) : (
          <form
            onSubmit={onSubmit}
            className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-xl"
          >
            <h2 className="text-lg font-semibold text-white">{t('register.title')}</h2>
            <p className="text-xs text-slate-500">{t('register.intro')}</p>
            <div>
              <label className={labelClass}>{t('register.displayName')}</label>
              <input
                type="text"
                required
                maxLength={100}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>{t('login.email')}</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={inputClass}
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className={labelClass}>{t('register.whatsapp')}</label>
              <input
                type="tel"
                required
                value={whatsapp}
                onChange={(e) => setWhatsapp(e.target.value)}
                className={inputClass}
                placeholder="+31612345678"
              />
              <p className="mt-1 text-xs text-slate-500">{t('register.whatsappNote')}</p>
            </div>
            <div>
              <label className={labelClass}>{t('login.password')}</label>
              <input
                type="password"
                required
                minLength={6}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClass}
                placeholder="••••••••"
              />
            </div>
            <div>
              <label className={labelClass}>{t('password.confirm')}</label>
              <input
                type="password"
                required
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className={inputClass}
                placeholder="••••••••"
              />
            </div>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-amber-400 disabled:opacity-50"
            >
              {busy ? t('register.submitting') : t('register.submit')}
            </button>
            <p className="text-center text-xs text-slate-500">
              {t('register.haveAccount')}{' '}
              <Link to="/login" className="font-medium text-amber-400 hover:underline">
                {t('register.signInLink')}
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  )
}
