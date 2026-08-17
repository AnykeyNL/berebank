import { Navigate, Route, Routes } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from './lib/auth'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import PortfolioPage from './pages/PortfolioPage'
import TradePage from './pages/TradePage'
import AnalyzePage from './pages/AnalyzePage'
import KimiAnalysisPage from './pages/KimiAnalysisPage'
import GTP56SolAnalysisPage from './pages/GTP56SolAnalysisPage'
import Fable5AnalysisPage from './pages/Fable5AnalysisPage'
import OpusAnalysisPage from './pages/OpusAnalysisPage'
import TradeHistoryPage from './pages/TradeHistoryPage'
import AdminPage from './pages/AdminPage'
import ProfilePage from './pages/ProfilePage'
import AiPage from './pages/AiPage'
import LeaderboardPage from './pages/LeaderboardPage'
import NewsPage from './pages/NewsPage'

export default function App() {
  const { user, loading } = useAuth()
  const { t } = useTranslation()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-slate-400">
        {t('common.loading')}
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<PortfolioPage />} />
        <Route path="/trade" element={<TradePage />} />
        <Route path="/trade/:market" element={<TradePage />} />
        <Route path="/technical-analysis" element={<AnalyzePage />} />
        <Route path="/technical-analysis/:market" element={<AnalyzePage />} />
        <Route path="/analyze/:market" element={<AnalyzePage />} />
        <Route path="/kimi-analysis" element={<KimiAnalysisPage />} />
        <Route path="/kimi-analysis/:market" element={<KimiAnalysisPage />} />
        <Route path="/gtp56sol-analysis" element={<GTP56SolAnalysisPage />} />
        <Route path="/gtp56sol-analysis/:market" element={<GTP56SolAnalysisPage />} />
        <Route path="/fable5-analysis" element={<Fable5AnalysisPage />} />
        <Route path="/fable5-analysis/:market" element={<Fable5AnalysisPage />} />
        <Route path="/opus-analysis" element={<OpusAnalysisPage />} />
        <Route path="/opus-analysis/:market" element={<OpusAnalysisPage />} />
        <Route path="/history" element={<TradeHistoryPage />} />
        <Route path="/leaderboard" element={<LeaderboardPage />} />
        <Route path="/news" element={<NewsPage />} />
        <Route path="/ai" element={<AiPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        {user.role === 'bank_manager' && <Route path="/admin" element={<AdminPage />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
