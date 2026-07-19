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

i18n.on('languageChanged', (lng) => {
  localStorage.setItem(LANG_KEY, lng)
  document.documentElement.lang = lng
})

export default i18n
