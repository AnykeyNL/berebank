import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import type { User } from '../lib/types'

const inputClass =
  'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-500'
const labelClass = 'mb-1 block text-sm font-medium text-slate-300'
const cardClass = 'rounded-xl border border-slate-800 bg-slate-900/60 p-6'
const buttonClass =
  'rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-amber-400 disabled:opacity-50'

type Feedback = { kind: 'success' | 'error'; text: string } | null

function FeedbackLine({ feedback }: { feedback: Feedback }) {
  if (!feedback) return null
  return (
    <p className={`text-sm ${feedback.kind === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>
      {feedback.text}
    </p>
  )
}

function ProfileForm() {
  const { user, updateUser } = useAuth()
  const { t, i18n } = useTranslation()
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [language, setLanguage] = useState<'en' | 'nl'>(
    user?.preferred_language ?? (i18n.language.startsWith('nl') ? 'nl' : 'en'),
  )
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFeedback(null)
    setBusy(true)
    try {
      const updated = await api<User>('/auth/profile', {
        method: 'PUT',
        body: JSON.stringify({ display_name: displayName.trim(), preferred_language: language }),
      })
      updateUser(updated)
      if (!i18n.language.startsWith(language)) void i18n.changeLanguage(language)
      setFeedback({ kind: 'success', text: t('profile.saved') })
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : t('profile.saveFailed'),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className={cardClass}>
      <h2 className="mb-4 text-lg font-semibold text-white">{t('profile.profileSection')}</h2>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className={labelClass}>{t('login.email')}</label>
          <input type="email" value={user?.email ?? ''} disabled className={`${inputClass} opacity-60`} />
        </div>
        <div>
          <label className={labelClass}>{t('profile.displayName')}</label>
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
          <label className={labelClass}>{t('profile.language')}</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value as 'en' | 'nl')}
            className={inputClass}
          >
            <option value="nl">{t('profile.dutch')}</option>
            <option value="en">{t('profile.english')}</option>
          </select>
          <p className="mt-1 text-xs text-slate-500">{t('profile.languageNote')}</p>
        </div>
        <FeedbackLine feedback={feedback} />
        <button type="submit" disabled={busy} className={buttonClass}>
          {busy ? t('profile.saving') : t('profile.save')}
        </button>
      </form>
    </section>
  )
}

function McpSection() {
  const { user, updateUser } = useAuth()
  const { t } = useTranslation()
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const mcpUrl = `${window.location.origin}/mcp`

  async function copyUrl() {
    await navigator.clipboard.writeText(mcpUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  async function toggleTrading(enabled: boolean) {
    setFeedback(null)
    setBusy(true)
    try {
      const updated = await api<User>('/auth/profile', {
        method: 'PUT',
        body: JSON.stringify({ mcp_trading_enabled: enabled }),
      })
      updateUser(updated)
      setFeedback({ kind: 'success', text: t('mcp.saved') })
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : t('mcp.saveFailed'),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className={cardClass}>
      <h2 className="mb-4 text-lg font-semibold text-white">{t('mcp.title')}</h2>
      <p className="mb-4 text-sm text-slate-400">{t('mcp.intro')}</p>
      <div className="mb-4">
        <label className={labelClass}>{t('mcp.serverUrl')}</label>
        <div className="flex gap-2">
          <input type="text" readOnly value={mcpUrl} className={`${inputClass} font-mono`} />
          <button
            type="button"
            onClick={copyUrl}
            className="shrink-0 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-amber-500 hover:text-amber-400"
          >
            {copied ? t('mcp.copied') : t('mcp.copy')}
          </button>
        </div>
      </div>
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={user?.mcp_trading_enabled ?? false}
          disabled={busy}
          onChange={(e) => void toggleTrading(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-amber-500"
        />
        <span>
          <span className="block text-sm font-medium text-slate-200">{t('mcp.tradingToggle')}</span>
          <span className="block text-xs text-slate-500">{t('mcp.tradingNote')}</span>
        </span>
      </label>
      <div className="mt-3">
        <FeedbackLine feedback={feedback} />
      </div>
    </section>
  )
}

function PasswordForm() {
  const { t } = useTranslation()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFeedback(null)
    if (newPassword.length < 6) {
      setFeedback({ kind: 'error', text: t('password.tooShort') })
      return
    }
    if (newPassword !== confirmPassword) {
      setFeedback({ kind: 'error', text: t('password.mismatch') })
      return
    }
    setBusy(true)
    try {
      await api<void>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setFeedback({ kind: 'success', text: t('password.changed') })
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : t('password.failed'),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className={cardClass}>
      <h2 className="mb-4 text-lg font-semibold text-white">{t('password.title')}</h2>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className={labelClass}>{t('password.current')}</label>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>{t('password.new')}</label>
          <input
            type="password"
            required
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className={inputClass}
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
          />
        </div>
        <FeedbackLine feedback={feedback} />
        <button type="submit" disabled={busy} className={buttonClass}>
          {busy ? t('password.saving') : t('password.save')}
        </button>
      </form>
    </section>
  )
}

export default function ProfilePage() {
  const { t } = useTranslation()

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-2xl font-bold text-white">{t('profile.title')}</h1>
      <ProfileForm />
      <McpSection />
      <PasswordForm />
    </div>
  )
}
