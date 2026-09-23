import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DatabaseBackup, EyeOff, FolderOpen, KeyRound, Laptop, Save, ScanLine, ShieldCheck } from 'lucide-react'
import { api } from '../api'
import { PasswordConfirmDialog } from '../components/PasswordConfirmDialog'
import { ErrorState, LoadingState } from '../components/States'
import { useAuth } from '../useAuth'

interface SettingsData {
  deepseek_api_key_configured: boolean; deepseek_api_key_masked: string; deepseek_base_url: string; deepseek_model: string; deepseek_model_display: string
  daily_new_words: number; article_length: number; ocr_language: string; ocr_use_gpu: boolean
  paddleocr_available: boolean; paddleocr_message: string; data_directory: string; database_path: string; backups_directory: string
  can_manage_instance_settings: boolean; onboarding_seen: boolean
}

/** One row of `GET /api/auth/sessions`: metadata only, never a token or its hash. */
interface SessionRow {
  id: number; current: boolean; created_at: string | null; last_seen_at: string | null; expires_at: string | null; user_agent: string
}

const moment = (value: string | null) => (value ? new Date(value).toLocaleString() : '—')

export function SettingsPage() {
  const client = useQueryClient()
  const { changePassword } = useAuth()
  const query = useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsData>('/api/settings') })
  const [form, setForm] = useState<Record<string, string | number | boolean>>({})
  useEffect(() => { if (query.data) setForm({ deepseek_api_key: '', deepseek_base_url: query.data.deepseek_base_url, deepseek_model: query.data.deepseek_model, daily_new_words: query.data.daily_new_words, article_length: query.data.article_length, ocr_language: query.data.ocr_language, ocr_use_gpu: query.data.ocr_use_gpu }) }, [query.data])
  const personalFields = ['daily_new_words', 'article_length']
  const instanceFields = ['deepseek_api_key', 'deepseek_base_url', 'deepseek_model', 'ocr_language', 'ocr_use_gpu']
  const save = useMutation({ mutationFn: () => {
    const isAdmin = Boolean(query.data?.can_manage_instance_settings)
    const payload = Object.fromEntries(Object.entries(form).filter(([key, value]) => {
      if (key === 'deepseek_api_key') return isAdmin && Boolean(value)
      if (instanceFields.includes(key)) return isAdmin
      return personalFields.includes(key)
    }))
    return api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) })
  }, onSuccess: () => client.invalidateQueries({ queryKey: ['settings'] }) })
  const backup = useMutation({ mutationFn: () => api<{ filename: string }>('/api/settings/backup', { method: 'POST' }) })
  const isAdmin = Boolean(query.data?.can_manage_instance_settings)
  const set = (key: string, value: string | number | boolean) => setForm((current) => ({ ...current, [key]: value }))

  // --- signed-in devices (F-1) ---------------------------------------------
  // The server scopes this list by the calling session and answers with metadata
  // only, so nothing rendered here can be a token or its hash.
  const [confirming, setConfirming] = useState<{ kind: 'one'; id: number; agent: string } | { kind: 'others' } | null>(null)
  const [deviceNotice, setDeviceNotice] = useState('')
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => api<{ sessions: SessionRow[] }>('/api/auth/sessions') })
  const revokeOne = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      api(`/api/auth/sessions/${id}`, { method: 'DELETE', body: JSON.stringify({ current_password: password }) }),
    onSuccess: () => { setConfirming(null); setDeviceNotice('已撤销该设备，它需要重新登录。'); void client.invalidateQueries({ queryKey: ['sessions'] }) },
  })
  const revokeOthers = useMutation({
    mutationFn: (password: string) =>
      api<{ revoked: number }>('/api/auth/sessions/revoke', { method: 'POST', body: JSON.stringify({ scope: 'others', current_password: password }) }),
    onSuccess: (result) => { setConfirming(null); setDeviceNotice(`已退出 ${result.revoked} 台其它设备。`); void client.invalidateQueries({ queryKey: ['sessions'] }) },
  })

  // --- own password (F-7) --------------------------------------------------
  const [passwords, setPasswords] = useState({ current: '', next: '', confirm: '' })
  const [passwordProblem, setPasswordProblem] = useState('')
  const change = useMutation({ mutationFn: () => changePassword(passwords.current, passwords.next) })

  function submitPassword(event: React.FormEvent) {
    event.preventDefault()
    setPasswordProblem('')
    if (passwords.next.length < 8) return setPasswordProblem('新密码至少 8 位。')
    if (passwords.next !== passwords.confirm) return setPasswordProblem('两次输入的新密码不一致。')
    if (passwords.next === passwords.current) return setPasswordProblem('新密码不能与当前密码相同。')
    change.mutate()
  }

  if (query.isLoading) return <LoadingState label="正在读取本地设置…" />
  if (query.isError) return <ErrorState error={query.error} />
  const data = query.data!
  const passwordError = passwordProblem || (change.error instanceof Error ? change.error.message : '')
  return (
    <div className="page settings-page">
      <header className="page-head"><h1>设置</h1><p>模型、OCR 和学习目标都保存在本机。API Key 不会返回明文。</p></header>
      <div className="settings-grid">
        {isAdmin ? (
          <section className="settings-section">
            <header><KeyRound size={20} /><div><h2>DeepSeek</h2><p>用于结构化、语义锚点、阅读文章与可选辅助判断。这是整个实例共享的配置。</p></div></header>
            <div className="form-grid">
              <label className="full">API Key <span>{data.deepseek_api_key_configured ? `已配置 ${data.deepseek_api_key_masked}` : '尚未配置'}</span><div className="secret-input"><input type="password" value={String(form.deepseek_api_key ?? '')} placeholder={data.deepseek_api_key_configured ? '留空则保持不变' : '输入 DeepSeek API Key'} onChange={(event) => set('deepseek_api_key', event.target.value)} /><EyeOff size={17} /></div></label>
              <label>Base URL<input value={String(form.deepseek_base_url ?? '')} onChange={(event) => set('deepseek_base_url', event.target.value)} /></label>
              <label>Model<input list="deepseek-models" value={String(form.deepseek_model ?? '')} onChange={(event) => set('deepseek_model', event.target.value)} /><datalist id="deepseek-models"><option value="deepseek-flash" /><option value="deepseek-v4-pro" /></datalist><small className="model-identity">当前对应：{data.deepseek_model_display}；推荐使用 API 名 deepseek-flash。</small></label>
            </div>
          </section>
        ) : null}
        <section className="settings-section">
          <header><ScanLine size={20} /><div><h2>学习与 OCR</h2><p>学习目标属于你个人；OCR 引擎由管理员配置，这里仅显示状态。</p></div></header>
          <div className="form-grid">
            <label>每日新词目标<input type="number" min="1" max="100" value={Number(form.daily_new_words ?? 15)} onChange={(event) => set('daily_new_words', Number(event.target.value))} /></label>
            <label>默认文章长度<input type="number" min="300" max="1200" value={Number(form.article_length ?? 650)} onChange={(event) => set('article_length', Number(event.target.value))} /></label>
            {isAdmin ? (
              <>
                <label>OCR 语言<input value={String(form.ocr_language ?? 'en')} onChange={(event) => set('ocr_language', event.target.value)} /></label>
                <label className="toggle-label"><input type="checkbox" checked={Boolean(form.ocr_use_gpu)} onChange={(event) => set('ocr_use_gpu', event.target.checked)} />启用 GPU（若 Paddle 支持）</label>
              </>
            ) : (
              <label className="full">OCR 配置<span className="readonly-value">{data.ocr_language}{data.ocr_use_gpu ? ' · GPU' : ''}（仅管理员可修改）</span></label>
            )}
          </div>
          <div className={data.paddleocr_available ? 'provider-status ok' : 'provider-status warning'}>{data.paddleocr_message}</div>
        </section>
        <section className="settings-section">
          <header><Laptop size={20} /><div><h2>登录设备</h2><p>每个登录中的浏览器或设备各占一条会话。撤销后该设备需要重新登录，学习记录不受影响。</p></div></header>
          {sessions.isLoading ? (
            <LoadingState label="正在读取登录设备…" />
          ) : sessions.isError ? (
            <ErrorState error={sessions.error} />
          ) : (sessions.data?.sessions ?? []).length === 0 ? (
            <p className="provider-status warning">暂时没有可显示的会话。</p>
          ) : (
            <ul className="session-list">
              {(sessions.data?.sessions ?? []).map((row) => (
                <li key={row.id} className={row.current ? 'session-row current' : 'session-row'}>
                  <div className="session-head">
                    <strong title={row.user_agent || undefined}>{row.user_agent || '未知设备'}</strong>
                    {row.current && <span className="session-badge">当前设备</span>}
                  </div>
                  <dl className="session-meta">
                    <div><dt>最近活动</dt><dd>{moment(row.last_seen_at)}</dd></div>
                    <div><dt>登录时间</dt><dd>{moment(row.created_at)}</dd></div>
                    <div><dt>到期时间</dt><dd>{moment(row.expires_at)}</dd></div>
                  </dl>
                  {row.current ? (
                    <p className="session-hint">当前设备不能在这里撤销；要结束这次登录，请用左下角的「退出」。</p>
                  ) : (
                    <button className="button secondary small" type="button" onClick={() => { setDeviceNotice(''); setConfirming({ kind: 'one', id: row.id, agent: row.user_agent || '未知设备' }) }}>撤销该设备</button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="session-actions">
            <button className="button secondary" type="button" disabled={revokeOthers.isPending} onClick={() => { setDeviceNotice(''); setConfirming({ kind: 'others' }) }}>退出其它所有设备</button>
            {deviceNotice && <p className="success-message" role="status">{deviceNotice}</p>}
          </div>
        </section>
        <section className="settings-section">
          <header><ShieldCheck size={20} /><div><h2>修改密码</h2><p>修改成功会撤销包括当前设备在内的全部会话，需要用新密码重新登录。</p></div></header>
          <form className="form-grid" aria-label="修改密码" onSubmit={submitPassword}>
            <label className="full">当前密码<input name="current_password" type="password" autoComplete="current-password" value={passwords.current} onChange={(event) => setPasswords({ ...passwords, current: event.target.value })} required /></label>
            <div><label>新密码<input name="new_password" type="password" autoComplete="new-password" minLength={8} value={passwords.next} onChange={(event) => setPasswords({ ...passwords, next: event.target.value })} required /></label><small className="model-identity">至少 8 位，且不能用当前密码。</small></div>
            <label>确认新密码<input name="confirm_password" type="password" autoComplete="new-password" value={passwords.confirm} onChange={(event) => setPasswords({ ...passwords, confirm: event.target.value })} required /></label>
            <div className="full password-submit">
              <button className="button primary" type="submit" disabled={change.isPending}>{change.isPending ? '正在修改…' : '修改密码'}</button>
              {passwordError && <span className="inline-error" role="alert">{passwordError}</span>}
            </div>
          </form>
        </section>
        <section className="settings-section data-section">
          <header><FolderOpen size={20} /><div><h2>本地数据</h2><p>数据库、图片、配置与备份集中存放，不散落在源码中。</p></div></header>
          <dl><div><dt>数据目录</dt><dd>{data.data_directory}</dd></div><div><dt>数据库</dt><dd>{data.database_path}</dd></div><div><dt>备份目录</dt><dd>{data.backups_directory}</dd></div></dl>
          {isAdmin ? (
            <>
              <button className="button secondary" disabled={backup.isPending} onClick={() => backup.mutate()}><DatabaseBackup size={17} />{backup.isPending ? '正在备份…' : '立即手动备份'}</button>
              {backup.data && <p className="success-message">已创建：{backup.data.filename}</p>}
              {backup.isError && <p className="inline-error">{backup.error.message}</p>}
            </>
          ) : (
            <p className="provider-status">整库备份由管理员执行。</p>
          )}
        </section>
      </div>
      <div className="settings-save"><span>{save.isSuccess ? '设置已保存' : save.isError ? save.error.message : '修改仅保存在当前设备'}</span><button className="button primary" disabled={save.isPending} onClick={() => save.mutate()}><Save size={17} />{save.isPending ? '正在保存…' : '保存设置'}</button></div>
      {confirming && (
        <PasswordConfirmDialog
          title={confirming.kind === 'one' ? '撤销这台设备' : '退出其它所有设备'}
          description={confirming.kind === 'one'
            ? `将结束「${confirming.agent}」的登录。该设备需要重新登录，学习记录不受影响。`
            : '将结束除当前设备外的全部登录。其它设备需要重新登录，学习记录不受影响。'}
          confirmLabel={confirming.kind === 'one' ? '确认撤销' : '确认退出'}
          pending={revokeOne.isPending || revokeOthers.isPending}
          error={confirming.kind === 'one' ? revokeOne.error : revokeOthers.error}
          onCancel={() => { setConfirming(null); revokeOne.reset(); revokeOthers.reset() }}
          onConfirm={(password) => {
            if (confirming.kind === 'one') revokeOne.mutate({ id: confirming.id, password })
            else revokeOthers.mutate(password)
          }}
        />
      )}
    </div>
  )
}
