import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { ImportPage } from './pages/ImportPage'
import { LibraryPage } from './pages/LibraryPage'
import { ReadingPage } from './pages/ReadingPage'
import { SettingsPage } from './pages/SettingsPage'
import { StudyPage } from './pages/StudyPage'
import './styles.css'

export function App() {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 20_000, retry: 1 } } }))
  return <QueryClientProvider client={client}><BrowserRouter><AppShell><Routes><Route path="/" element={<DashboardPage />} /><Route path="/import" element={<ImportPage />} /><Route path="/study" element={<StudyPage />} /><Route path="/reading" element={<ReadingPage />} /><Route path="/library" element={<LibraryPage />} /><Route path="/settings" element={<SettingsPage />} /></Routes></AppShell></BrowserRouter></QueryClientProvider>
}
