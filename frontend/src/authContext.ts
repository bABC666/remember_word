import { createContext } from 'react'

/**
 * The authenticated user, as reported by the server.
 *
 * Only display data is kept in memory. The session token itself lives in an
 * HttpOnly cookie and is deliberately never readable by this code, and nothing
 * about the user is written to localStorage.
 */
export interface CurrentUser {
  id: number
  username: string
  display_name: string
  role: string
  is_admin: boolean
  settings: {
    daily_new_words: number
    article_length: number
    onboarding_seen: boolean
  }
}

export interface AuthState {
  user: CurrentUser | null
  /** True until the initial `/api/auth/me` check settles. */
  checking: boolean
  /** Set when the session ended for a reason worth explaining, such as a password change. */
  notice: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /**
   * Change the caller's own password. Every session is revoked server-side, this
   * one included, so the client returns to the sign-in screen.
   */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  refresh: () => Promise<void>
}

/** Shared with `auth.tsx`; kept here so the component file exports only components. */
export const AuthContext = createContext<AuthState | null>(null)
