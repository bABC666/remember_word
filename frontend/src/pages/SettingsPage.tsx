import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DatabaseBackup, EyeOff, FolderOpen, KeyRound, ScanLine, Save } from 'lucide-react'
import { api } from '../api'
import { ErrorState, LoadingState } from '../components/States'

interface SettingsData {
  deepseek_api_key_configured: boolean; deepseek_api_key_masked: string; deepseek_base_url: string; deepseek_model: string; deepseek_model_display: string
  daily_new_words: number; article_length: number; ocr_language: string; ocr_use_gpu: boolean
  paddleocr_available: boolean; paddleocr_message: string; data_directory: string; database_path: string; backups_directory: string
}

export function SettingsPage() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsData>('/api/settings') })
  const [form, setForm] = useState<Record<string, string | number | boolean>>({})
  useEffect(() => { if (query.data) setForm({ deepseek_api_key: '', deepseek_base_url: query.data.deepseek_base_url, deepseek_model: query.data.deepseek_model, daily_new_words: query.data.daily_new_words, article_length: query.data.article_length, ocr_language: query.data.ocr_language, ocr_use_gpu: query.data.ocr_use_gpu }) }, [query.data])
  const save = useMutation({ mutationFn: () => api('/api/settings', { method: 'PUT', body: JSON.stringify(Object.fromEntries(Object.entries(form).filter(([key, value]) => key !== 'deepseek_api_key' || value))) }), onSuccess: () => client.invalidateQueries({ queryKey: ['settings'] }) })
  const backup = useMutation({ mutationFn: () => api<{ filename: string }>('/api/settings/backup', { method: 'POST' }) })
  const set = (key: string, value: string | number | boolean) => setForm((current) => ({ ...current, [key]: value }))
  if (query.isLoading) return <LoadingState label="正在读取本地设置…" />
  if (query.isError) return <ErrorState error={query.error} />
  const data = query.data!
  return (
    <div className="page settings-page">
      <header className="page-head"><h1>设置</h1><p>模型、OCR 和学习目标都保存在本机。API Key 不会返回明文。</p></header>
      <div className="settings-grid">
        <section className="settings-section"><header><KeyRound size={20} /><div><h2>DeepSeek</h2><p>用于结构化、语义锚点、阅读文章与可选辅助判断。</p></div></header><div className="form-grid"><label className="full">API Key <span>{data.deepseek_api_key_configured ? `已配置 ${data.deepseek_api_key_masked}` : '尚未配置'}</span><div className="secret-input"><input type="password" value={String(form.deepseek_api_key ?? '')} placeholder={data.deepseek_api_key_configured ? '留空则保持不变' : '输入 DeepSeek API Key'} onChange={(event) => set('deepseek_api_key', event.target.value)} /><EyeOff size={17} /></div></label><label>Base URL<input value={String(form.deepseek_base_url ?? '')} onChange={(event) => set('deepseek_base_url', event.target.value)} /></label><label>Model<input list="deepseek-models" value={String(form.deepseek_model ?? '')} onChange={(event) => set('deepseek_model', event.target.value)} /><datalist id="deepseek-models"><option value="deepseek-flash" /><option value="deepseek-v4-pro" /></datalist><small className="model-identity">当前对应：{data.deepseek_model_display}；推荐使用 API 名 deepseek-flash。</small></label></div></section>
        <section className="settings-section"><header><ScanLine size={20} /><div><h2>学习与 OCR</h2><p>第一版调度保持透明；PaddleOCR 是默认识别引擎。</p></div></header><div className="form-grid"><label>每日新词目标<input type="number" min="1" max="100" value={Number(form.daily_new_words ?? 15)} onChange={(event) => set('daily_new_words', Number(event.target.value))} /></label><label>默认文章长度<input type="number" min="300" max="1200" value={Number(form.article_length ?? 650)} onChange={(event) => set('article_length', Number(event.target.value))} /></label><label>OCR 语言<input value={String(form.ocr_language ?? 'en')} onChange={(event) => set('ocr_language', event.target.value)} /></label><label className="toggle-label"><input type="checkbox" checked={Boolean(form.ocr_use_gpu)} onChange={(event) => set('ocr_use_gpu', event.target.checked)} />启用 GPU（若 Paddle 支持）</label></div><div className={data.paddleocr_available ? 'provider-status ok' : 'provider-status warning'}>{data.paddleocr_message}</div></section>
        <section className="settings-section data-section"><header><FolderOpen size={20} /><div><h2>本地数据</h2><p>数据库、图片、配置与备份集中存放，不散落在源码中。</p></div></header><dl><div><dt>数据目录</dt><dd>{data.data_directory}</dd></div><div><dt>数据库</dt><dd>{data.database_path}</dd></div><div><dt>备份目录</dt><dd>{data.backups_directory}</dd></div></dl><button className="button secondary" disabled={backup.isPending} onClick={() => backup.mutate()}><DatabaseBackup size={17} />{backup.isPending ? '正在备份…' : '立即手动备份'}</button>{backup.data && <p className="success-message">已创建：{backup.data.filename}</p>}{backup.isError && <p className="inline-error">{backup.error.message}</p>}</section>
      </div>
      <div className="settings-save"><span>{save.isSuccess ? '设置已保存' : save.isError ? save.error.message : '修改仅保存在当前设备'}</span><button className="button primary" disabled={save.isPending} onClick={() => save.mutate()}><Save size={17} />{save.isPending ? '正在保存…' : '保存设置'}</button></div>
    </div>
  )
}
