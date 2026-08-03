import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

export type AnalysisKind = 'technical' | 'kimi' | 'gtp56sol' | 'fable5' | 'opus'

const LINKS: { id: AnalysisKind; path: string; labelKey: string }[] = [
  { id: 'technical', path: '/technical-analysis', labelKey: 'analyze.analyzeButton' },
  { id: 'kimi', path: '/kimi-analysis', labelKey: 'kimiAnalysis.button' },
  { id: 'gtp56sol', path: '/gtp56sol-analysis', labelKey: 'gtp56solAnalysis.button' },
  { id: 'fable5', path: '/fable5-analysis', labelKey: 'fable5Analysis.button' },
  { id: 'opus', path: '/opus-analysis', labelKey: 'opusAnalysis.button' },
]

export default function AnalysisCrossLinks({
  market,
  current,
}: {
  market: string
  current: AnalysisKind
}) {
  const { t } = useTranslation()
  const others = LINKS.filter((link) => link.id !== current)

  return (
    <span
      role="group"
      aria-label={t('analysisLinks.label')}
      className="contents"
    >
      {others.map((link) => (
        <Link key={link.id} to={`${link.path}/${market}`} className="text-slate-400 hover:text-slate-200">
          {t(link.labelKey)} →
        </Link>
      ))}
    </span>
  )
}
