import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../lib/auth'
import LanguageSwitcher from './LanguageSwitcher'

const menuLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive ? 'bg-amber-500/15 text-amber-400' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
  }`

export default function Layout() {
  const { user, logout } = useAuth()
  const { t } = useTranslation()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [menuOpen])

  const navLinks = (
    <>
      <NavLink to="/" end className={menuLinkClass} onClick={() => setMenuOpen(false)}>
        {t('nav.portfolio')}
      </NavLink>
      <NavLink to="/trade" className={menuLinkClass} onClick={() => setMenuOpen(false)}>
        {t('nav.trade')}
      </NavLink>
      <NavLink to="/history" className={menuLinkClass} onClick={() => setMenuOpen(false)}>
        {t('nav.history')}
      </NavLink>
      <NavLink to="/leaderboard" className={menuLinkClass} onClick={() => setMenuOpen(false)}>
        {t('nav.leaderboard')}
      </NavLink>
      <NavLink to="/ai" className={menuLinkClass} onClick={() => setMenuOpen(false)}>
        {t('nav.ai')}
      </NavLink>
      {user?.role === 'bank_manager' && (
        <NavLink to="/admin" className={menuLinkClass} onClick={() => setMenuOpen(false)}>
          {t('nav.admin')}
        </NavLink>
      )}
    </>
  )

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                onClick={() => setMenuOpen((open) => !open)}
                aria-expanded={menuOpen}
                aria-haspopup="true"
                aria-label={t('nav.menu')}
                className="rounded-md p-1.5 text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-5 w-5"
                  aria-hidden="true"
                >
                  <line x1="4" x2="20" y1="6" y2="6" />
                  <line x1="4" x2="20" y1="12" y2="12" />
                  <line x1="4" x2="20" y1="18" y2="18" />
                </svg>
              </button>
              {menuOpen && (
                <nav
                  className="absolute left-0 top-full z-20 mt-1 min-w-48 rounded-md border border-slate-700 bg-slate-900 py-1 shadow-lg"
                  role="menu"
                >
                  {navLinks}
                </nav>
              )}
            </div>
            <NavLink
              to="/"
              end
              title={t('nav.portfolio')}
              className="flex items-center gap-2 rounded-md transition-opacity hover:opacity-80"
            >
              <span className="text-xl">🐻</span>
              <span className="text-lg font-bold tracking-tight text-amber-400">de BereBank</span>
            </NavLink>
          </div>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <LanguageSwitcher />
            <span className="text-slate-400">
              {user?.display_name}
              {user?.role === 'bank_manager' && (
                <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-400">
                  BankManager
                </span>
              )}
            </span>
            <NavLink
              to="/profile"
              title={t('profile.title')}
              className={({ isActive }) =>
                `rounded-md p-1.5 transition-colors hover:bg-slate-800 ${
                  isActive ? 'text-amber-400' : 'text-slate-400 hover:text-white'
                }`
              }
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4"
                aria-hidden="true"
              >
                <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </NavLink>
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
    </div>
  )
}
