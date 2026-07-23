import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import nl from './locales/nl.json'

const LANG_KEY = 'berebank_lang'

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    nl: { translation: nl },
  },
  lng: localStorage.getItem(LANG_KEY) ?? 'nl',
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React already escapes
})

function syncDocumentLanguage(lng: string) {
  const lang = lng.startsWith('nl') ? 'nl' : 'en'
  document.documentElement.lang = lang
}

i18n.on('languageChanged', (lng) => {
  localStorage.setItem(LANG_KEY, lng)
  syncDocumentLanguage(lng)
})

syncDocumentLanguage(i18n.language)

export default i18n
