import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { ErrorState, LoadingState } from '../components/States'

export interface LexiconChoice {
  id: number
  name: string
  description: string
  entry_count: number
  is_system: boolean
  source_type: string
  enabled: boolean | null
}

interface PreviewRow { line: number; word: string; meaning: string; part_of_speech: string; status: 'valid' | 'duplicate' | 'error'; reason: string }
interface Preview { rows: PreviewRow[]; counts: Record<'valid' | 'duplicate' | 'error', number> }

export function LexiconsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [imported, setImported] = useState<number | null>(null)
  const lexicons = useQuery({ queryKey: ['lexicons'], queryFn: () => api<LexiconChoice[]>('/api/lexicons') })
  const selectLexicon = useMutation({
    mutationFn: (id: number) => api(`/api/lexicons/${id}/enable`, { method: 'POST' }),
    onSuccess: (_, id) => navigate(`/study?lexicon_id=${id}`),
  })
  const previewFile = useMutation({
    mutationFn: (chosen: File) => {
      const form = new FormData()
      form.append('file', chosen)
      return api<Preview>('/api/lexicons/file-preview', { method: 'POST', body: form })
    },
    onSuccess: setPreview,
  })
  const importFile = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append('file', file!)
      form.append('name', name.trim())
      return api<{ imported_count: number }>('/api/lexicons/file-import', { method: 'POST', body: form })
    },
    onSuccess: async (result) => {
      setImported(result.imported_count)
      setPreview(null)
      setFile(null)
      await queryClient.invalidateQueries({ queryKey: ['lexicons'] })
    },
  })
  return <div className="page lexicons-page">
    <header className="page-head"><h1>选择词库</h1><p>选择要学习的系统词库或你的私有词库。各词库的学习记录独立保存。</p></header>
    {lexicons.isLoading && <LoadingState />}
    {lexicons.isError && <ErrorState error={lexicons.error} />}
    {lexicons.data && <section className="lexicon-cards" aria-label="可用词库">
      {lexicons.data.length === 0 && <p>暂无可用词库。你可以先导入自己的单词。</p>}
      {[...lexicons.data].sort((a, b) => Number(b.is_system && /NETEM/i.test(b.name)) - Number(a.is_system && /NETEM/i.test(a.name))).map((item) => <article key={item.id}>
        <h2>{item.name} {item.is_system && /NETEM/i.test(item.name) && <small>推荐</small>}</h2><p>{item.description}</p>
        <p>{item.entry_count} 个单词 · {item.is_system ? '系统词库' : '仅本人可见'}</p>
        <button className="button secondary" disabled={selectLexicon.isPending} onClick={() => selectLexicon.mutate(item.id)}>学习这个词库</button>
      </article>)}
    </section>}
    {selectLexicon.isError && <ErrorState error={selectLexicon.error} />}
    <section className="file-lexicon-import">
      <h2>导入自定义词库</h2>
      <p>TXT 每行一个英文单词；CSV 需要 word（或 单词）列，可选 meaning（释义）、part_of_speech（词性）。文件需为 UTF-8，最多 1 MB、10000 行。</p>
      <p>你提供的释义会标明“用户提供，未核实”，不会与平台来源合并。</p>
      <label>词库名称 <input aria-label="词库名称" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} /></label>
      <label>选择 TXT 或 CSV 文件 <input aria-label="选择 TXT 或 CSV 文件" type="file" accept=".txt,.csv,text/plain,text/csv" onChange={(event) => {
        const chosen = event.target.files?.[0] ?? null
        setFile(chosen); setPreview(null); setImported(null)
        if (chosen) previewFile.mutate(chosen)
      }} /></label>
      {previewFile.isPending && <p>正在预览…</p>}
      {previewFile.isError && <ErrorState error={previewFile.error} />}
      {preview && <div>
        <p>有效 {preview.counts.valid} 行 · 重复 {preview.counts.duplicate} 行 · 错误 {preview.counts.error} 行</p>
        <div className="file-preview-list" role="list">
          {preview.rows.map((row) => <div role="listitem" key={row.line}>
            <span>第 {row.line} 行</span> <strong>{row.word || '（空）'}</strong> <span>{row.meaning}</span> <span>{row.part_of_speech}</span>
            <span>{row.status === 'valid' ? '有效' : row.status === 'duplicate' ? '重复' : '错误'} {row.reason}</span>
          </div>)}
        </div>
        <button className="button primary" disabled={!name.trim() || !preview.counts.valid || importFile.isPending}
          onClick={() => importFile.mutate()}>确认导入 {preview.counts.valid} 个单词</button>
      </div>}
      {importFile.isError && <ErrorState error={importFile.error} />}
      {imported !== null && <p role="status">已导入 {imported} 个单词。可在上方选择词库开始学习。</p>}
    </section>
  </div>
}
