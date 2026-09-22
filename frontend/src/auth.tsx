import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { AuthContext, type AuthState, type CurrentUser } from './authContext'
import { onUnauthorized } from './session'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [checking, setChecking] = useState(true)
  // Guards against a stale response re-instating a session that has already been
  // dropped, which is exactly how one user's data could flash on screen for the
  // next user.
  const generation = useRef(0)

  const clearSession = useCallback(() => {
    generation.current += 1
    // Every cached response belongs to the user who fetched it. Clearing the
    // whole cache (not just invalidating) is what makes switching accounts safe:
    // there is no window in which the next user can see the previous one's data.
    queryClient.clear()
    setUser(null)
  }, [queryClient])

  const refresh = useCallback(async () => {
    const current = generation.current
    try {
      const next = await api<CurrentUser>('/api/auth/me')
      if (generation.current === current) setUser(next)
    } catch {
      if (generation.current === current) clearSession()
    }
  }, [clearSession])

  // A 401 from any request drops the session once, through the same path as an
  // explicit logout.
  useEffect(() => {
    onUnauthorized(clearSession)
    return () => onUnauthorized(null)
  }, [clearSession])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      await refresh()
      if (!cancelled) setChecking(false)
    })()
    return () => {
      cancelled = true
    }
  }, [refresh])

  const login = useCallback(
    async (username: string, password: string) => {
      const next = await api<CurrentUser>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      // Anything cached belongs to whoever was logged in before.
      queryClient.clear()
      generation.current += 1
      setUser(next)
    },
    [queryClient],
  )

  const logout = useCallback(async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
    } catch {
      // Even if the server call fails, the client must drop the session.
    } finally {
      clearSession()
    }
  }, [clearSession])

  const value = useMemo<AuthState>(
    () => ({ user, checking, login, logout, refresh }),
    [user, checking, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
