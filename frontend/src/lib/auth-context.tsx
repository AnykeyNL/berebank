import { createContext, useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import i18n from '../i18n'
import { api, getToken, setToken } from './api'
import type { User } from './types'

export interface AuthState {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  updateUser: (user: User) => void
}

export const AuthContext = createContext<AuthState | null>(null)

function applyPreferredLanguage(user: User) {
  if (user.preferred_language && !i18n.language.startsWith(user.preferred_language)) {
    void i18n.changeLanguage(user.preferred_language)
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }
    api<User>('/auth/me')
      .then((u) => {
        applyPreferredLanguage(u)
        setUser(u)
      })
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await api<{ access_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    setToken(access_token)
    const u = await api<User>('/auth/me')
    applyPreferredLanguage(u)
    setUser(u)
  }, [])

  const updateUser = useCallback((u: User) => setUser(u), [])

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  )
}
