import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReadingPage } from './ReadingPage'

const article = {
  id: 7,
  title: 'Memory and Cities',
  content: 'Cities retain traces of the people who built them.',
  created_at: '2026-09-22T00:00:00Z',
  target_words: ['retain'],
  completed: false,
  translation: '',
  translated_at: null,
  lookup_history: [],
}

function json(data: unknown) {
  return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

afterEach(() => vi.restoreAllMocks())

describe('ReadingPage assistance', () => {
  it('looks up a clicked word and keeps an on-demand translation', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url === '/api/articles') return json([article])
      if (url.endsWith('/lookup')) return json({ id: 11, article_id: 7, surface: 'retain', normalized_word: 'retain', phonetic: '/rɪˈteɪn/', part_of_speech: 'v.', meaning: '保留', explanation: '在此处指城市保留痕迹。', context: article.content, source: 'wordbook', added_word_id: 3, created_at: article.created_at })
      if (url.endsWith('/translate')) return json({ ...article, translation: '城市保留了建造者留下的痕迹。', translated_at: article.created_at, lookup_history: [] })
      if (url === '/api/articles/7') return json(article)
      throw new Error(`unexpected request ${url} ${init?.method ?? 'GET'}`)
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><ReadingPage /></MemoryRouter></QueryClientProvider>)

    fireEvent.click(await screen.findByRole('button', { name: 'retain' }))
    expect(await screen.findByText('保留')).toBeInTheDocument()
    expect(screen.getByText('/rɪˈteɪn/')).toBeInTheDocument()
    expect(screen.getByText('已在词库')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '显示全文翻译' }))
    expect(await screen.findByText('城市保留了建造者留下的痕迹。')).toBeInTheDocument()
  })
})
