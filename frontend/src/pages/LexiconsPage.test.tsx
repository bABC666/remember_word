import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { LexiconsPage } from './LexiconsPage'

afterEach(() => vi.unstubAllGlobals())

it('previews file rows and imports only after confirmation', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const path = String(input)
    calls.push(path)
    if (path === '/api/lexicons') return Promise.resolve(Response.json([]))
    if (path === '/api/lexicons/selection') return Promise.resolve(Response.json({ lexicon_id: null, source: 'none' }))
    if (path.endsWith('/file-preview')) return Promise.resolve(Response.json({
      sha256: 'preview-digest',
      rows: [
        { line: 1, word: 'apple', meaning: '苹果', part_of_speech: 'n.', status: 'valid', reason: '' },
        { line: 2, word: 'apple', meaning: '', part_of_speech: '', status: 'duplicate', reason: '文件内重复' },
      ], counts: { valid: 1, duplicate: 1, error: 0 },
    }))
    if (path.endsWith('/file-import')) return Promise.resolve(Response.json({ id: 41, name: '私有', imported_count: 1 }))
    throw new Error(path)
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><LexiconsPage /></MemoryRouter>
  </QueryClientProvider>)
  await userEvent.type(screen.getByLabelText('词库名称'), '私有')
  await userEvent.upload(screen.getByLabelText('选择 TXT 或 CSV 文件'), new File(['word,meaning\napple,苹果'], 'words.csv', { type: 'text/csv' }))
  expect(await screen.findByText(/重复 文件内重复/)).toBeInTheDocument()
  expect(calls).not.toContain('/api/lexicons/file-import')
  await userEvent.click(screen.getByRole('button', { name: '确认导入 1 个单词' }))
  expect(await screen.findByText(/已导入 1 个单词/)).toBeInTheDocument()
})

it('recommends NETEM only when the system lexicon exists and enables the chosen list', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const path = String(input)
    calls.push(path)
    if (path === '/api/lexicons') return Promise.resolve(Response.json([
      { id: 2, name: '我的词库', description: '', entry_count: 1, is_system: false, source_type: 'user_file', enabled: true },
      { id: 1, name: 'NETEM 考研词库', description: '', entry_count: 10, is_system: true, source_type: 'kaoyan', enabled: null },
    ]))
    if (path === '/api/lexicons/selection') return Promise.resolve(Response.json({ lexicon_id: 1, source: 'recommended' }))
    if (path === '/api/lexicons/1/select') return Promise.resolve(Response.json({ lexicon_id: 1, source: 'explicit' }))
    throw new Error(path)
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><LexiconsPage /></MemoryRouter>
  </QueryClientProvider>)
  expect(await screen.findByText('推荐')).toBeInTheDocument()
  await userEvent.click(screen.getAllByRole('button', { name: '学习这个词库' })[0])
  expect(calls).toContain('/api/lexicons/1/select')
})

it('keeps only the latest file preview and imports exactly its confirmed contents', async () => {
  const pending: Record<string, (response: Response) => void> = {}
  let importedFile = ''
  let importedDigest = ''
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/api/lexicons') return Promise.resolve(Response.json([]))
    if (path === '/api/lexicons/selection') return Promise.resolve(Response.json({ lexicon_id: null, source: 'none' }))
    if (path.endsWith('/file-preview')) {
      const file = (init?.body as FormData).get('file') as File
      return new Promise<Response>((resolve) => { pending[file.name] = resolve })
    }
    if (path.endsWith('/file-import')) {
      const form = init?.body as FormData
      importedDigest = String(form.get('preview_sha256'))
      importedFile = (form.get('file') as File).name
      return Promise.resolve(Response.json({ imported_count: 1 }, { status: 201 }))
    }
    throw new Error(path)
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><LexiconsPage /></MemoryRouter>
  </QueryClientProvider>)
  await userEvent.type(screen.getByLabelText('词库名称'), '切换文件')
  const input = screen.getByLabelText('选择 TXT 或 CSV 文件')
  await userEvent.upload(input, new File(['apple\n'], 'a.txt'))
  await userEvent.upload(input, new File(['banana\n'], 'b.txt'))
  await waitFor(() => expect(pending['b.txt']).toBeDefined())
  pending['b.txt'](Response.json({
    sha256: 'digest-b',
    rows: [{ line: 1, word: 'banana', meaning: '', part_of_speech: '', status: 'valid', reason: '' }],
    counts: { valid: 1, duplicate: 0, error: 0 },
  }))
  expect(await screen.findByText('banana')).toBeInTheDocument()
  await act(async () => pending['a.txt'](Response.json({
    sha256: 'digest-a',
    rows: [{ line: 1, word: 'apple', meaning: '', part_of_speech: '', status: 'valid', reason: '' }],
    counts: { valid: 1, duplicate: 0, error: 0 },
  })))
  expect(screen.queryByText('apple')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '确认导入 1 个单词' }))
  expect(await screen.findByText(/已导入 1 个单词/)).toBeInTheDocument()
  expect(importedFile).toBe('b.txt')
  expect(importedDigest).toBe('digest-b')
})
