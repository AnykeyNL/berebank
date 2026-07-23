import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

type FullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null
  webkitExitFullscreen?: () => Promise<void>
}

type FullscreenElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void>
}

function getFullscreenElement(): Element | null {
  const doc = document as FullscreenDocument
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null
}

function isFullscreenSupported(): boolean {
  const el = document.documentElement as FullscreenElement
  return !!(el.requestFullscreen || el.webkitRequestFullscreen)
}

export default function FullscreenButton() {
  const { t } = useTranslation()
  const [isFullscreen, setIsFullscreen] = useState(() => !!getFullscreenElement())
  const [supported] = useState(isFullscreenSupported)

  useEffect(() => {
    if (!supported) return

    const onChange = () => setIsFullscreen(!!getFullscreenElement())
    document.addEventListener('fullscreenchange', onChange)
    document.addEventListener('webkitfullscreenchange', onChange)
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      document.removeEventListener('webkitfullscreenchange', onChange)
    }
  }, [supported])

  const toggle = useCallback(async () => {
    const doc = document as FullscreenDocument
    const el = document.documentElement as FullscreenElement

    try {
      if (getFullscreenElement()) {
        await (doc.exitFullscreen?.() ?? doc.webkitExitFullscreen?.())
      } else {
        await (el.requestFullscreen?.() ?? el.webkitRequestFullscreen?.())
      }
    } catch {
      // Browser blocked fullscreen (unsupported platform or user denied).
    }
  }, [])

  if (!supported) return null

  const label = isFullscreen ? t('nav.exitFullscreen') : t('nav.enterFullscreen')

  return (
    <button
      type="button"
      onClick={toggle}
      title={label}
      aria-label={label}
      className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-white"
    >
      {isFullscreen ? (
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
          <path d="M8 3v3a2 2 0 0 1-2 2H3" />
          <path d="M21 8h-3a2 2 0 0 1-2-2V3" />
          <path d="M3 16h3a2 2 0 0 1 2 2v3" />
          <path d="M16 21v-3a2 2 0 0 1 2-2h3" />
        </svg>
      ) : (
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
          <path d="M8 3H5a2 2 0 0 0-2 2v3" />
          <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
          <path d="M3 16v3a2 2 0 0 0 2 2h3" />
          <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
        </svg>
      )}
    </button>
  )
}
