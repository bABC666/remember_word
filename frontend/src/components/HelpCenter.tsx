import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenCheck, CircleHelp, Database, ScanText, Sparkles, X } from 'lucide-react'
import { api } from '../api'

interface HelpCenterProps {
  /**
   * Bumped by the phone "更多" sheet, which is the only help entry below 620px.
   *
   * The dialog's own open state stays here: the onboarding prompt and the
   * sidebar entry both need it, and a caller only ever has to ask for the dialog
   * to appear, not to own it.
   */
  openRequest?: number
}

export function HelpCenter({ openRequest = 0 }: HelpCenterProps = {}) {
  const client = useQueryClient()
  const [manualOpen, setManualOpen] = useState(false)
  const onboarding = useQuery({ queryKey: ['onboarding'], queryFn: () => api<{ seen: boolean }>('/api/settings/onboarding'), staleTime: Infinity })
  const dismiss = useMutation({
    mutationFn: () => api<{ seen: boolean }>('/api/settings/onboarding', { method: 'POST' }),
    onSuccess: () => { client.setQueryData(['onboarding'], { seen: true }); setManualOpen(false) },
  })
  const open = manualOpen || onboarding.data?.seen === false
  const close = () => onboarding.data?.seen === false ? dismiss.mutate() : setManualOpen(false)

  useEffect(() => {
    if (openRequest > 0) setManualOpen(true)
  }, [openRequest])

  return (
    <>
      <button className="help-entry" type="button" onClick={() => setManualOpen(true)}><CircleHelp size={18} /><span>使用说明</span></button>
      {open && <div className="help-overlay" role="presentation">
        <section className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title">
          <button className="help-close" aria-label="关闭使用说明" onClick={close}><X size={19} /></button>
          <div className="help-kicker">欢迎来到拾词</div>
          <h2 id="help-title">欢迎使用拾词</h2>
          <p className="help-lead">这不是一套让你机械刷次数的系统。它把原书信息、主动回忆和真实阅读连接起来，让每个词逐渐成为你能认出、理解并使用的词。</p>
          <div className="help-principles">
            <article><ScanText size={20} /><div><h3>原书是事实，AI 是助手</h3><p>OCR 原文和照片永久保留。AI 只整理候选、生成简短锚点；你确认后才进入词库。</p></div></article>
            <article><BookOpenCheck size={20} /><div><h3>先回忆，再看答案</h3><p>看到单词时先自己想。不会、模糊、会都要诚实选择，系统才能安排下一次复习。</p></div></article>
            <article><Sparkles size={20} /><div><h3>在阅读中再次遇见</h3><p>阅读文章不追求硬塞全部单词。自然语境、点词查询和阅读后判断共同形成长期记忆。</p></div></article>
            <article><Database size={20} /><div><h3>记录留在本机</h3><p>词库、复习历史、文章、翻译和查词记录都保存在本地 SQLite，并每天自动备份。</p></div></article>
          </div>
          <footer><button className="button primary" disabled={dismiss.isPending} onClick={close}>{dismiss.isPending ? '正在保存…' : '开始使用'}</button></footer>
        </section>
      </div>}
    </>
  )
}
