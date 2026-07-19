import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDateTime, fmtEur } from '../lib/format'
import type { AdminUser, Settings } from '../lib/types'

export default function AdminPage() {
  return (
    <div className="space-y-8">
      <UserManagement />
      <BitvavoSettings />
      <TwelveDataSettings />
    </div>
  )
}

function UserManagement() {
  const { t } = useTranslation()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api<AdminUser[]>('/admin/users').then(setUsers).catch((e) => setError(e.message))
  }, [])

  useEffect(load, [load])

  return (
    <section>
      <h2 className="mb-4 text-xl font-bold">{t('admin.userManagement')}</h2>
      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <CreateUserForm onCreated={load} />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60">
          <h3 className="border-b border-slate-800 px-4 py-3 font-semibold">{t('admin.accounts')}</h3>
          {error && <p className="px-4 py-3 text-sm text-red-400">{error}</p>}
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-4 py-2">{t('admin.user')}</th>
                <th className="px-4 py-2">{t('admin.role')}</th>
                <th className="px-4 py-2 text-right">{t('admin.balance')}</th>
                <th className="px-4 py-2">{t('admin.created')}</th>
                <th className="px-4 py-2">{t('admin.status')}</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <UserRow key={u.id} user={u} onChanged={load} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

function UserRow({ user, onChanged }: { user: AdminUser; onChanged: () => void }) {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()
  const [editing, setEditing] = useState(false)
  const [balance, setBalance] = useState('')
  const canDelete = user.role !== 'bank_manager' && user.id !== currentUser?.id

  async function saveBalance() {
    await api(`/admin/users/${user.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ balance_eur: balance }),
    })
    setEditing(false)
    onChanged()
  }

  async function toggleActive() {
    await api(`/admin/users/${user.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !user.is_active }),
    })
    onChanged()
  }

  async function deleteUser() {
    if (!window.confirm(t('admin.deleteConfirm', { name: user.display_name }))) return
    await api(`/admin/users/${user.id}`, { method: 'DELETE' })
    onChanged()
  }

  return (
    <tr className="border-t border-slate-800/60">
      <td className="px-4 py-2.5">
        <span className="font-medium">{user.display_name}</span>
        <span className="block text-xs text-slate-500">{user.email}</span>
      </td>
      <td className="px-4 py-2.5">
        {user.role === 'bank_manager' ? (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-400">BankManager</span>
        ) : (
          <span className="text-slate-400">{t('admin.roleUser').toLowerCase()}</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-right font-mono">
        {editing ? (
          <span className="flex items-center justify-end gap-1">
            <input
              type="number"
              step="0.01"
              min="0"
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
              className="w-28 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right text-xs outline-none focus:border-amber-500"
            />
            <button onClick={saveBalance} className="text-xs text-emerald-400 hover:underline">
              {t('admin.save')}
            </button>
            <button onClick={() => setEditing(false)} className="text-xs text-slate-400 hover:underline">
              ×
            </button>
          </span>
        ) : (
          <button
            onClick={() => {
              setBalance(parseFloat(user.balance_eur).toFixed(2))
              setEditing(true)
            }}
            title={t('admin.editBalanceTitle')}
            className="hover:text-amber-400"
          >
            {fmtEur(user.balance_eur)}
          </button>
        )}
      </td>
      <td className="px-4 py-2.5 text-slate-400">{fmtDateTime(user.created_at)}</td>
      <td className="px-4 py-2.5">
        {user.is_active ? (
          <span className="text-emerald-400">{t('admin.active')}</span>
        ) : (
          <span className="text-red-400">{t('admin.disabled')}</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-right">
        <div className="flex justify-end gap-1.5">
          <button
            onClick={toggleActive}
            className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            {user.is_active ? t('admin.disable') : t('admin.enable')}
          </button>
          {canDelete && (
            <button
              onClick={deleteUser}
              className="rounded border border-red-900/60 px-2 py-0.5 text-xs text-red-400 hover:bg-red-950/40"
            >
              {t('admin.delete')}
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [balance, setBalance] = useState('10000')
  const [role, setRole] = useState<'user' | 'bank_manager'>('user')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setBusy(true)
    try {
      await api('/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          email,
          password,
          display_name: displayName,
          role,
          initial_balance_eur: balance || '0',
        }),
      })
      setSuccess(t('admin.createdFor', { name: displayName }))
      setEmail('')
      setDisplayName('')
      setPassword('')
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('admin.createFailed'))
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-amber-500'

  return (
    <form onSubmit={onSubmit} className="h-fit space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="font-semibold">{t('admin.createAccount')}</h3>
      <div>
        <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.displayName')}</label>
        <input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} />
      </div>
      <div>
        <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('login.email')}</label>
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
      </div>
      <div>
        <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('login.password')}</label>
        <input
          type="text"
          required
          minLength={6}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={inputClass}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.initialBalance')}</label>
          <input
            type="number"
            min="0"
            step="0.01"
            value={balance}
            onChange={(e) => setBalance(e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.role')}</label>
          <select value={role} onChange={(e) => setRole(e.target.value as 'user' | 'bank_manager')} className={inputClass}>
            <option value="user">{t('admin.roleUser')}</option>
            <option value="bank_manager">BankManager</option>
          </select>
        </div>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {success && <p className="text-sm text-emerald-400">{success}</p>}
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50"
      >
        {busy ? t('admin.creating') : t('admin.createAccount')}
      </button>
    </form>
  )
}

function BitvavoSettings() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api<Settings>('/admin/settings').then(setSettings).catch((e) => setError(e.message))
  }, [])

  useEffect(load, [load])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    setError(null)
    try {
      const body: Record<string, string> = {}
      if (apiKey) body.bitvavo_api_key = apiKey
      if (apiSecret) body.bitvavo_api_secret = apiSecret
      const updated = await api<Settings>('/admin/settings', { method: 'PUT', body: JSON.stringify(body) })
      setSettings(updated)
      setApiKey('')
      setApiSecret('')
      setMessage(t('admin.settingsSaved'))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('admin.settingsFailed'))
    }
  }

  const inputClass =
    'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-mono outline-none focus:border-amber-500'

  return (
    <section>
      <h2 className="mb-4 text-xl font-bold">{t('admin.bitvavoConnection')}</h2>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="mb-3 font-semibold">{t('admin.connStatus')}</h3>
          {settings ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.feed')}</dt>
                <dd>
                  {settings.connection.connected ? (
                    <span className="text-emerald-400">● {t('admin.connected')}</span>
                  ) : (
                    <span className="text-red-400">● {t('admin.disconnected')}</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.eurMarkets')}</dt>
                <dd className="font-mono">{settings.connection.markets}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.pricesCached')}</dt>
                <dd className="font-mono">{settings.connection.prices_cached}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.apiKey')}</dt>
                <dd className="font-mono">{settings.bitvavo_api_key_masked ?? t('admin.notSet')}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.apiSecret')}</dt>
                <dd className="font-mono">{settings.has_api_secret ? t('admin.isSet') : t('admin.notSet')}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-slate-400">{t('common.loading')}</p>
          )}
          <p className="mt-4 text-xs text-slate-500">{t('admin.credentialsNote')}</p>
        </div>
        <form onSubmit={onSubmit} className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="font-semibold">{t('admin.apiCredentials')}</h3>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.apiKey')}</label>
            <input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('admin.keepCurrent')}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.apiSecret')}</label>
            <input
              type="password"
              value={apiSecret}
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder={t('admin.keepCurrent')}
              className={inputClass}
            />
          </div>
          {message && <p className="text-sm text-emerald-400">{message}</p>}
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            className="rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400"
          >
            {t('admin.saveCredentials')}
          </button>
        </form>
      </div>
    </section>
  )
}

function TwelveDataSettings() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<Settings>('/admin/settings').then(setSettings).catch((e) => setError(e.message))
  }, [])

  useEffect(load, [load])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!apiKey) return
    setMessage(null)
    setError(null)
    setBusy(true)
    try {
      const updated = await api<Settings>('/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({ twelvedata_api_key: apiKey }),
      })
      setSettings(updated)
      setApiKey('')
      setMessage(t('admin.settingsSaved'))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('admin.settingsFailed'))
    } finally {
      setBusy(false)
    }
  }

  const td = settings?.twelvedata
  const inputClass =
    'w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-mono outline-none focus:border-amber-500'

  return (
    <section>
      <h2 className="mb-4 text-xl font-bold">{t('admin.twelvedataConnection')}</h2>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="mb-3 font-semibold">{t('admin.connStatus')}</h3>
          {settings && td ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.feed')}</dt>
                <dd>
                  {!td.configured ? (
                    <span className="text-slate-400">● {t('admin.notConfigured')}</span>
                  ) : td.connected ? (
                    <span className="text-emerald-400">● {t('admin.connected')}</span>
                  ) : (
                    <span className="text-red-400">● {t('admin.disconnected')}</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.stockFundMarkets')}</dt>
                <dd className="font-mono">{td.markets}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.pricesCached')}</dt>
                <dd className="font-mono">{td.prices_cached}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.usdEurRate')}</dt>
                <dd className="font-mono">{td.usd_eur ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">{t('admin.apiKey')}</dt>
                <dd className="font-mono">{settings.twelvedata_api_key_masked ?? t('admin.notSet')}</dd>
              </div>
              {td.error && (
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-400">{t('admin.lastError')}</dt>
                  <dd className="text-right text-red-400">{td.error}</dd>
                </div>
              )}
            </dl>
          ) : (
            <p className="text-sm text-slate-400">{t('common.loading')}</p>
          )}
          <p className="mt-4 text-xs text-slate-500">{t('admin.twelvedataNote')}</p>
        </div>
        <form onSubmit={onSubmit} className="h-fit space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="font-semibold">{t('admin.apiCredentials')}</h3>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t('admin.apiKey')}</label>
            <input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('admin.keepCurrent')}
              className={inputClass}
            />
          </div>
          {message && <p className="text-sm text-emerald-400">{message}</p>}
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={busy || !apiKey}
            className="rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50"
          >
            {t('admin.saveCredentials')}
          </button>
        </form>
      </div>
    </section>
  )
}
