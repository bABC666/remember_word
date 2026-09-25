import { useLayoutEffect, useRef, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AuthProvider } from './auth'
import { useAuth } from './useAuth'
import { AppShell } from './components/AppShell'
import { LoadingState } from './components/States'
import { DashboardPage } from './pages/DashboardPage'
import { ImportPage } from './pages/ImportPage'
import { LibraryPage } from './pages/LibraryPage'
import { WordDetailPage } from './pages/WordDetailPage'
import type { LibraryPosition } from './pages/wordDetailModel'
import { LoginPage } from './pages/LoginPage'
import { ReadingPage } from './pages/ReadingPage'
import { SettingsPage } from './pages/SettingsPage'
import { StudyPage } from './pages/StudyPage'
import './styles.css'

/**
 * Authentication is not retried: a 401 means the session is gone, and retrying
 * would only delay the return to the login screen.
 */
const NO_RETRY_ON_AUTH_FAILURE = (failureCount: number, error: unknown) => {
  const status = (error as { status?: number } | null)?.status
  if (status === 401 || status === 403) return false
  return failureCount < 1
}

function AuthenticatedApp() {
  const libraryPositions = useRef(new Map<string, LibraryPosition>())
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/import" element={<ImportPage />} />
        <Route path="/study" element={<StudyPage />} />
        <Route path="/reading" element={<ReadingPage />} />
        <Route path="/library" element={<LibraryPage positions={libraryPositions.current} />} />
        <Route path="/library/:wordStateId" element={<WordDetailPage positions={libraryPositions.current} />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  )
}

/** Chooses between the sign-in screen and the application. */
function Gate() {
  const { user, checking } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const hadAccount = useRef(false)
  const showedLogin = useRef(false)
  if (!checking && !user) showedLogin.current = true
  const libraryState = location.state as { libraryOwnerId?: number } | null
  const ownerId = libraryState?.libraryOwnerId
  const inLibrary = location.pathname === '/library'
  const foreignLibrary = Boolean(user && inLibrary && (
    showedLogin.current || (ownerId !== undefined && ownerId !== user.id)
  ))

  useLayoutEffect(() => {
    if (checking) return
    if (!user) {
      if (hadAccount.current && inLibrary) {
        navigate('/library', { replace: true, state: null })
        window.scrollTo(0, 0)
      }
      hadAccount.current = false
      return
    }
    hadAccount.current = true
    if (foreignLibrary) {
      showedLogin.current = false
      navigate('/library', { replace: true, state: { libraryOwnerId: user.id } })
      window.scrollTo(0, 0)
    } else if (inLibrary && ownerId === undefined) {
      navigate(location.pathname + location.search, {
        replace: true,
        state: { ...libraryState, libraryOwnerId: user.id },
      })
    } else {
      showedLogin.current = false
    }
  }, [checking, user, inLibrary, foreignLibrary, ownerId, location.pathname, location.search, libraryState, navigate])

  if (checking || foreignLibrary) return <LoadingState label="正在检查登录状态…" />
  if (!user) return <LoginPage />
  // `key` guarantees a fresh subtree per account, so no component state can
  // survive a user switch even before the query cache is cleared.
  return <AuthenticatedApp key={user.id} />
}

export function App() {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 20_000, retry: NO_RETRY_ON_AUTH_FAILURE } } }),
  )
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <BrowserRouter>
          <Gate />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
