import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Clock3, FileImage, LoaderCircle, Plus, Trash2, UploadCloud, X } from 'lucide-react'
import { api } from '../api'
import { ErrorState } from '../components/States'
import { fileKey, mergeImageFiles } from '../importFiles'
import type { Candidate, ImportBatch } from '../types'

const steps = ['上传图片', 'OCR 识别', '校对词条', '确认入库']

type ImportSummary = Pick<ImportBatch, 'id' | 'status' | 'stage' | 'created_at'>

export function ImportPage() {
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const batchInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const imports = useQuery({
    queryKey: ['imports'],
    queryFn: () => api<ImportSummary[]>('/api/imports'),
  })

  const upload = useMutation({
    mutationFn: async () => {
      const form = new FormData()
      files.forEach((file) => form.append('files', file))
      return api<ImportBatch>('/api/imports', { method: 'POST', body: form })
    },
    onSuccess: async (value) => { setBatch(value); setFiles([]); await queryClient.invalidateQueries({ queryKey: ['imports'] }) },
  })
  const ocr = useMutation({ mutationFn: () => api<ImportBatch>(`/api/imports/${batch!.id}/ocr`, { method: 'POST' }, { timeoutMs: 600_000 }), onSuccess: setBatch })
  const structure = useMutation({ mutationFn: () => api<ImportBatch>(`/api/imports/${batch!.id}/structure`, { method: 'POST' }), onSuccess: setBatch })
  const appendImages = useMutation({
    mutationFn: async (incoming: File[]) => {
      const form = new FormData()
      incoming.forEach((file) => form.append('files', file))
      return api<ImportBatch>(`/api/imports/${batch!.id}/images`, { method: 'POST', body: form })
    },
    onSuccess: setBatch,
  })
  const removeImage = useMutation({
    mutationFn: (imageId: number) => api<ImportBatch>(`/api/imports/${batch!.id}/images/${imageId}`, { method: 'DELETE' }),
    onSuccess: setBatch,
  })
  const abandon = useMutation({
    mutationFn: () => api(`/api/imports/${batch!.id}`, { method: 'DELETE' }),
    onSuccess: async () => { setBatch(null); await queryClient.invalidateQueries({ queryKey: ['imports'] }) },
  })
  const confirm = useMutation({
    mutationFn: () => api<{ created: number }>(`/api/imports/${batch!.id}/confirm`, { method: 'POST', body: JSON.stringify({ candidate_ids: batch!.candidates.filter((item) => item.selected && !item.confirmed).map((item) => item.id) }) }),
    onSuccess: async () => { await queryClient.invalidateQueries(); setBatch((value) => value ? { ...value, status: 'confirmed', stage: 'confirmed' } : value) },
  })
  const resume = useMutation({
    mutationFn: (id: number) => api<ImportBatch>(`/api/imports/${id}`),
    onSuccess: setBatch,
  })

  const updateCandidate = async (candidate: Candidate, changes: Partial<Candidate>) => {
    setBatch((value) => value ? { ...value, candidates: value.candidates.map((item) => item.id === candidate.id ? { ...item, ...changes } : item) } : value)
    try {
      await api(`/api/imports/${batch!.id}/candidates/${candidate.id}`, { method: 'PATCH', body: JSON.stringify(changes) })
    } catch (error) {
      setBatch((value) => value ? { ...value, error_message: error instanceof Error ? error.message : '保存失败' } : value)
    }
  }
  const addFiles = (incoming: File[]) => setFiles((current) => mergeImageFiles(current, incoming))
  const removeQueuedFile = (target: File) => setFiles((current) => current.filter((file) => fileKey(file) !== fileKey(target)))
  const abandonCurrent = () => {
    if (window.confirm('移除这个未完成批次？原始记录会保留在本地，界面中不再显示。')) abandon.mutate()
  }
  const stage = batch?.status === 'confirmed' ? 4 : batch?.candidates.length ? 3 : batch?.raw_ocr_text ? 2 : batch ? 1 : 0
  const needsOcr = Boolean(batch?.images.some((image) => !image.ocr_text))
  const activeError = upload.error ?? appendImages.error ?? removeImage.error ?? abandon.error ?? ocr.error ?? structure.error ?? confirm.error

  return (
    <div className="page import-page">
      <header className="page-head"><h1>从单词书导入</h1><p>上传单词书照片，识别后逐条校对，确认无误再加入词库。</p></header>
      <div className="steps">{steps.map((label, index) => <div key={label} className={stage >= index + 1 ? 'done' : stage === index ? 'active' : ''}><i>{stage > index + 1 ? <Check size={16} /> : index + 1}</i><span>{label}<small>{index === 2 ? '人工确认是必需步骤' : ''}</small></span></div>)}</div>
      {!batch && (
        <>
          <section className={`drop-zone ${dragging ? 'dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)) }}>
            <UploadCloud size={38} /><h2>拖入单词书照片</h2><p>支持 JPG、PNG、WEBP，可连续拖入或分多次选择。</p>
            <button className="button secondary" onClick={() => inputRef.current?.click()}>选择图片</button>
            <input ref={inputRef} hidden type="file" accept="image/*" multiple onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = '' }} />
            {files.length > 0 && <div className="file-list">{files.map((file) => <div key={fileKey(file)}><span><FileImage size={16} />{file.name}</span><button type="button" aria-label={`移除 ${file.name}`} onClick={() => removeQueuedFile(file)}><X size={15} /></button></div>)}</div>}
            <button className="button primary" disabled={!files.length || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? <><LoaderCircle className="spin" size={17} />正在保存图片…</> : `创建导入批次（${files.length} 张）`}</button>
          </section>
          {imports.data?.some((item) => item.status !== 'confirmed') && (
            <section className="resume-imports">
              <div><h2>继续未完成的导入</h2><p>图片、OCR 原文和已校对内容都保存在本地。</p></div>
              {imports.data.filter((item) => item.status !== 'confirmed').map((item) => (
                <button key={item.id} disabled={resume.isPending} onClick={() => resume.mutate(item.id)}>
                  <Clock3 size={18} /><span><strong>批次 #{item.id}</strong><small>{new Date(item.created_at).toLocaleString('zh-CN')} · {item.stage}</small></span><em>继续</em>
                </button>
              ))}
            </section>
          )}
        </>
      )}
      {batch && !batch.candidates.length && (
        <section className="import-progress">
          <div className="image-list"><div className="image-list-title"><h2>已保存的图片（{batch.images.length}）</h2><button className="icon-action danger" aria-label="移除当前导入批次" onClick={abandonCurrent}><Trash2 size={16} /></button></div>{batch.images.map((image) => <div key={image.id}><FileImage size={20} /><span>{image.original_name}<small>{image.width} × {image.height}</small></span>{image.ocr_text && <Check size={18} className="success" />}<button className="icon-action" aria-label={`删除 ${image.original_name}`} disabled={removeImage.isPending} onClick={() => removeImage.mutate(image.id)}><X size={15} /></button></div>)}<button className="add-image-button" disabled={appendImages.isPending} onClick={() => batchInputRef.current?.click()}><Plus size={16} />{appendImages.isPending ? '正在添加…' : '继续添加照片'}</button><input ref={batchInputRef} hidden type="file" accept="image/*" multiple onChange={(event) => { const incoming = Array.from(event.target.files ?? []).filter((file) => file.type.startsWith('image/')); if (incoming.length) appendImages.mutate(incoming); event.currentTarget.value = '' }} /></div>
          <div className="process-panel">
            <h2>{batch.raw_ocr_text && !needsOcr ? 'OCR 原始结果已安全保存' : batch.images.length ? '图片已安全保存' : '请先添加图片'}</h2>
            <p>{batch.raw_ocr_text && !needsOcr ? '下一步由 DeepSeek 排除页码、栏目标题等噪声，整理为候选词条；OCR 原文不会被覆盖。' : batch.images.length ? 'PaddleOCR 会复用已完成图片的结果，只识别新增或失败的图片。' : '可以继续添加正确的单词书照片，也可以移除这个批次。'}</p>
            {batch.raw_ocr_text && <pre>{batch.raw_ocr_text}</pre>}
            {(!batch.raw_ocr_text || needsOcr) ? <button className="button primary" disabled={ocr.isPending || !batch.images.length} onClick={() => ocr.mutate()}>{ocr.isPending ? '正在 OCR…' : batch.raw_ocr_text ? '识别新增图片' : '开始 OCR 识别'}</button> : <button className="button primary" disabled={structure.isPending} onClick={() => structure.mutate()}>{structure.isPending ? '正在结构化…' : 'AI 清理并生成候选词条'}</button>}
          </div>
        </section>
      )}
      {batch && batch.candidates.length > 0 && (
        <section className="candidate-review">
          <div className="review-head"><div><h2>识别到 {batch.candidates.length} 个词条</h2><p>AI 已排除明显噪声；仍请逐项校对，勾选后才会入库。</p></div><div className="review-meta"><span>批次 #{batch.id}</span>{batch.status !== 'confirmed' && <button className="text-button danger" onClick={abandonCurrent}><Trash2 size={14} />放弃批次</button>}{batch.status === 'confirmed' && <button className="text-button" onClick={() => setBatch(null)}>开始新的导入</button>}</div></div>
          <div className="candidate-table">
            <div className="candidate-header"><span>选择</span><span>单词 / 音标</span><span>词性</span><span>原书完整释义</span><span>Anchor</span><span>疑点</span></div>
            {batch.candidates.map((candidate) => (
              <div className="candidate-row" key={candidate.id}>
                <label className="check"><input type="checkbox" checked={candidate.selected} disabled={candidate.confirmed} onChange={(event) => updateCandidate(candidate, { selected: event.target.checked })} /><span /></label>
                <div className="stacked-input"><input aria-label="单词" value={candidate.word} onChange={(event) => updateCandidate(candidate, { word: event.target.value })} /><input className="phonetic" aria-label="音标" value={candidate.phonetic} placeholder="音标" onChange={(event) => updateCandidate(candidate, { phonetic: event.target.value })} /></div>
                <input aria-label="词性" value={candidate.part_of_speech} onChange={(event) => updateCandidate(candidate, { part_of_speech: event.target.value })} />
                <textarea aria-label="原书完整释义" value={candidate.source_meanings.join('\n')} onChange={(event) => updateCandidate(candidate, { source_meanings: event.target.value.split('\n').filter(Boolean) })} />
                <textarea aria-label="Anchor" value={candidate.anchor} onChange={(event) => updateCandidate(candidate, { anchor: event.target.value })} />
                <div className={candidate.possible_issue ? 'issue' : 'no-issue'}>{candidate.possible_issue ? <><AlertTriangle size={17} />{candidate.issue_note || '请重点检查'}</> : '—'}</div>
              </div>
            ))}
          </div>
          <footer className="confirm-bar"><strong>已选择 {batch.candidates.filter((item) => item.selected && !item.confirmed).length} 个词条</strong><div><span>修改会自动保存</span><button className="button primary" disabled={confirm.isPending || batch.status === 'confirmed'} onClick={() => confirm.mutate()}>{batch.status === 'confirmed' ? '已确认入库' : confirm.isPending ? '正在入库…' : '确认入库'}</button></div></footer>
        </section>
      )}
      {(activeError || resume.error) && <ErrorState error={activeError ?? resume.error} />}
      {batch?.error_message && <div className="persistent-error"><AlertTriangle size={18} /><div><strong>{batch.error_stage.toUpperCase()} 阶段未完成</strong><p>{batch.error_message}</p><small>批次、图片、OCR 原文和你的修改都已保留，可以稍后重试。</small></div></div>}
    </div>
  )
}
