import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../lib/auth'
import LanguageSwitcher from './LanguageSwitcher'

const iconProps = {
  xmlns: 'http://www.w3.org/2000/svg',
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  className: 'h-5 w-5',
  'aria-hidden': true,
} as const

function PortfolioIcon() {
  return (
    <svg {...iconProps}>
      <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
      <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
      <path d="M18 12a2 2 0 0 0 0 4h4v-4Z" />
    </svg>
  )
}

function TradeIcon() {
  return (
    <svg {...iconProps}>
      <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
      <polyline points="16 7 22 7 22 13" />
    </svg>
  )
}

function HistoryIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </svg>
  )
}

function LeaderboardIcon() {
  return (
    <svg {...iconProps}>
      <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
      <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
      <path d="M4 22h16" />
      <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
      <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
      <path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" />
    </svg>
  )
}

function MoreIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </svg>
  )
}

const tabClass = (active: boolean) =>
  `flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium transition-colors ${
    active ? 'text-amber-400' : 'text-slate-400'
  }`

const sheetLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2.5 text-sm font-medium transition-colors ${
    isActive ? 'bg-amber-500/15 text-amber-400' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
  }`

const MORE_ROUTES = ['/news', '/ai', '/admin', '/profile']

export default function MobileTabBar() {
  const { user, logout } = useAuth()
  const { t } = useTranslation()
  const location = useLocation()
  const [sheetOpen, setSheetOpen] = useState(false)

  // Close the More sheet whenever the route changes.
  useEffect(() => {
    setSheetOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!sheetOpen) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSheetOpen(false)
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [sheetOpen])

  const moreActive = MORE_ROUTES.some((r) => location.pathname.startsWith(r))

  return (
    <div className="md:hidden">
      {sheetOpen && (
        <div className="fixed inset-0 z-30">
          <button
            type="button"
            aria-label={t('common.cancel')}
            onClick={() => setSheetOpen(false)}
            className="absolute inset-0 bg-slate-950/60"
          />
          <div
            role="menu"
            className="absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-slate-700 bg-slate-900 p-3 pb-[calc(env(safe-area-inset-bottom)+4.5rem)] shadow-2xl"
          >
            <div className="mb-2 flex items-center justify-between border-b border-slate-800 px-3 pb-3">
              <span className="text-sm text-slate-300">
                {user?.display_name}
                {user?.role === 'bank_manager' && (
                  <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-400">
                    BankManager
                  </span>
                )}
              </span>
              <LanguageSwitcher />
            </div>
            <nav className="space-y-0.5">
              <NavLink to="/news" className={sheetLinkClass}>
                {t('nav.news')}
              </NavLink>
              <NavLink to="/ai" className={sheetLinkClass}>
                {t('nav.ai')}
              </NavLink>
              {user?.role === 'bank_manager' && (
                <NavLink to="/admin" className={sheetLinkClass}>
                  {t('nav.admin')}
                </NavLink>
              )}
              <NavLink to="/profile" className={sheetLinkClass}>
                {t('profile.title')}
              </NavLink>
            </nav>
            <button
              type="button"
              onClick={logout}
              className="mt-3 w-full rounded-md border border-slate-700 px-3 py-2.5 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800"
            >
              {t('nav.logout')}
            </button>
          </div>
        </div>
      )}
      <nav
        aria-label={t('nav.menu')}
        className="fixed inset-x-0 bottom-0 z-40 flex border-t border-slate-800 bg-slate-950/95 pb-[env(safe-area-inset-bottom)] backdrop-blur"
      >
        <NavLink to="/" end className={({ isActive }) => tabClass(isActive && !sheetOpen)}>
          <PortfolioIcon />
          {t('nav.portfolio')}
        </NavLink>
        <NavLink to="/trade" className={({ isActive }) => tabClass(isActive && !sheetOpen)}>
          <TradeIcon />
          {t('nav.trade')}
        </NavLink>
        <NavLink to="/history" className={({ isActive }) => tabClass(isActive && !sheetOpen)}>
          <HistoryIcon />
          {t('nav.historyShort')}
        </NavLink>
        <NavLink to="/leaderboard" className={({ isActive }) => tabClass(isActive && !sheetOpen)}>
          <LeaderboardIcon />
          {t('nav.leaderboard')}
        </NavLink>
        <button
          type="button"
          onClick={() => setSheetOpen((open) => !open)}
          aria-expanded={sheetOpen}
          aria-haspopup="true"
          className={tabClass(sheetOpen || moreActive)}
        >
          <MoreIcon />
          {t('nav.more')}
        </button>
      </nav>
    </div>
  )
}
