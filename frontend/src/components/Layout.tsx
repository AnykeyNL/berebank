import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../lib/auth'
import LanguageSwitcher from './LanguageSwitcher'
import ChangePasswordDialog from './ChangePasswordDialog'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive ? 'bg-amber-500/15 text-amber-400' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
  }`

export default function Layout() {
  const { user, logout } = useAuth()
  const { t } = useTranslation()
  const [showPasswordDialog, setShowPasswordDialog] = useState(false)

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-xl">🐻</span>
            <span className="text-lg font-bold tracking-tight text-amber-400">de BereBank</span>
          </div>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={linkClass}>
              {t('nav.portfolio')}
            </NavLink>
            <NavLink to="/trade" className={linkClass}>
              {t('nav.trade')}
            </NavLink>
            <NavLink to="/history" className={linkClass}>
              {t('nav.history')}
            </NavLink>
            {user?.role === 'bank_manager' && (
              <NavLink to="/admin" className={linkClass}>
                {t('nav.admin')}
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <LanguageSwitcher />
            <button
              onClick={() => setShowPasswordDialog(true)}
              title={t('password.title')}
              className="text-slate-400 transition-colors hover:text-amber-400"
            >
              {user?.display_name}
              {user?.role === 'bank_manager' && (
                <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-400">
                  BankManager
                </span>
              )}
            </button>
            <button
              onClick={logout}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-slate-300 transition-colors hover:bg-slate-800"
            >
              {t('nav.logout')}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
      {showPasswordDialog && <ChangePasswordDialog onClose={() => setShowPasswordDialog(false)} />}
    </div>
  )
}
