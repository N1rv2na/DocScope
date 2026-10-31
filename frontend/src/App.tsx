import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CitationView, type Citation } from './CitationView'
import { consumeAgentStream, StreamInterruptedError } from './agentStream'

type DocumentRecord = {
  document_id: string
  filename: string
  status: 'processing' | 'ready' | 'failed' | 'deleting'
  error_code?: string | null
  chunk_count?: number | null
}
type UploadResponse = {
  status?: string
  filename?: string
  document_id?: string
  chunk_count?: number
  error_code?: string
}
type ChatResponse = {
  status?: string
  answer?: string | null
  citations?: Citation[]
  run_id?: string
  error_code?: string
}
type ChatResult =
  | { kind: 'answered'; answer: string; citations: Citation[]; runId: string }
  | { kind: 'insufficient' }
  | { kind: 'error'; message: string }
type AgentStage = {
  stage: string
  status: string
  round?: number
  query?: string
  next_query?: string
  evidence_ids?: string[]
  new_evidence_ids?: string[]
}
type AgentProgress = { runId: string; stages: AgentStage[]; text: string; citations: Citation[] }

const uploadErrors: Record<string, string> = {
  parse_failed: '文档索引失败：没有可检索的正文。请检查文件内容。',
  embedding_failed: '文档索引失败：本地向量模型未完成处理，请重试。',
  index_failed: '文档索引失败：检索索引不可用，请重试。',
  storage_failed: '文档索引失败：无法保存源文件。',
  state_failed: '文档索引失败：无法保存导入状态。',
  interrupted: '导入被中断，请重新上传此文档。',
  delete_failed: '删除未完成，文档已不可检索，请重试删除。',
}
const documentStatuses = { processing: '处理中', ready: '索引就绪', failed: '失败', deleting: '删除中' }
const chatErrors: Record<string, string> = {
  model_failed: '模型服务调用失败，请稍后重试。',
  model_output_invalid: '模型返回了无法验证的结果，请重试。',
  retrieval_failed: '检索服务暂时不可用，请稍后重试。',
  context_limit_exceeded: '问题和证据超出本轮上下文限制，请缩短问题。',
  source_changed: '回答期间文档已发生变化，请重新提问。',
  planner_failed: '查询规划失败，请稍后重试。',
  plan_output_invalid: '模型返回的查询规划无法验证，请重试。',
  run_record_failed: '无法保存本轮运行记录，请重试。',
  stream_failed: '回答流处理失败，请重试。',
}
const stopReasons: Record<string, string> = {
  answered: '已回答', no_evidence: '未找到证据', no_new_evidence: '第二轮没有新证据',
  repeated_query: '查询重复', missing_fact_unresolved: '缺失事实无法继续检索',
  retrieval_budget_exhausted: '检索预算耗尽', model_abstained: '模型拒答',
}
const focus = 'focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-[#317a53]'

async function jsonBody<T>(response: Response): Promise<T | null> {
  try { return await response.json() as T } catch { return null }
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentsError, setDocumentsError] = useState<string | null>(null)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [replacement, setReplacement] = useState<DocumentRecord | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [citationRefresh, setCitationRefresh] = useState(0)
  const [question, setQuestion] = useState('')
  const [questionError, setQuestionError] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<ChatResult | null>(null)
  const [agentProgress, setAgentProgress] = useState<AgentProgress | null>(null)
  const [agentStopReason, setAgentStopReason] = useState<string | null>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const uploadButtonRef = useRef<HTMLButtonElement>(null)
  const askButtonRef = useRef<HTMLButtonElement>(null)
  const agentButtonRef = useRef<HTMLButtonElement>(null)
  const agentAbortRef = useRef<AbortController | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const documentsHeadingRef = useRef<HTMLHeadingElement>(null)
  const documentsRequest = useRef(0)

  useEffect(() => () => agentAbortRef.current?.abort(), [])

  const refreshDocuments = useCallback(async () => {
    const request = ++documentsRequest.current
    try {
      const response = await fetch('/api/documents')
      const data = await jsonBody<{ documents: DocumentRecord[] }>(response)
      if (!response.ok || !Array.isArray(data?.documents)) throw new Error('invalid list')
      if (request === documentsRequest.current) { setDocuments(data.documents); setDocumentsError(null) }
    } catch {
      if (request === documentsRequest.current) setDocumentsError('无法读取文档状态，请刷新重试。')
    }
  }, [])

  useEffect(() => {
    void refreshDocuments()
    const onFocus = () => { void refreshDocuments(); setCitationRefresh(value => value + 1) }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshDocuments])

  useEffect(() => {
    if (!uploading && !deleting) return
    const timer = window.setInterval(() => { void refreshDocuments() }, 1000)
    return () => window.clearInterval(timer)
  }, [uploading, deleting, refreshDocuments])

  async function deleteDocument(record: DocumentRecord) {
    setDeleting(record.document_id)
    setOperationError(null)
    try {
      const response = await fetch(`/api/documents/${encodeURIComponent(record.document_id)}`, { method: 'DELETE' })
      const data = await jsonBody<{ status?: string }>(response)
      if (!response.ok || data?.status !== 'deleted') throw new Error('delete failed')
      if (replacement?.document_id === record.document_id) setReplacement(null)
    } catch { setOperationError('删除失败，请刷新状态后重试删除。') }
    finally {
      await refreshDocuments()
      setDeleting(null)
      setCitationRefresh(value => value + 1)
      requestAnimationFrame(() => documentsHeadingRef.current?.focus())
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setUploadError(null)
    setQuestionError(null)
    if (!file) { setUploadError('请选择 PDF 或 Markdown 文件。'); return }
    const restoreButtonFocus = window.document.activeElement === uploadButtonRef.current
    setUploading(true)
    setOperationError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch(replacement ? `/api/documents/${encodeURIComponent(replacement.document_id)}` : '/api/documents', { method: replacement ? 'PUT' : 'POST', body })
      const data = await jsonBody<UploadResponse>(response)
      if (response.ok && data?.status === 'ready' && data.filename && data.document_id) {
        setReplacement(null)
      } else if (data?.status === 'failed') {
        setUploadError(data.error_code === 'parse_failed' && file.name.toLowerCase().endsWith('.pdf')
          ? 'PDF 导入失败：无法解析可检索文本。请检查是否为扫描件、损坏文件或文档过长。'
          : uploadErrors[data.error_code ?? ''] ?? '文档索引失败，请稍后重试。')
      } else if (response.status === 413) {
        setUploadError('文件超过 1 MiB 上限，请选择更小的文件。')
      } else if (response.status === 415) {
        setUploadError('仅支持 PDF 或 Markdown 文件。')
      } else if (response.status === 409) {
        setUploadError('文档名称冲突，请从列表选择要替换的文档或更改文件名。')
      } else if (response.status === 404) {
        setUploadError('要替换的文档已不存在，请刷新文档列表。')
      } else {
        setUploadError('文件无效或上传失败，请检查内容后重试。')
      }
    } catch {
      setUploadError('无法连接文档服务，请检查后端是否启动。')
    } finally {
      await refreshDocuments()
      setUploading(false)
      setCitationRefresh(value => value + 1)
      if (restoreButtonFocus) requestAnimationFrame(() => uploadButtonRef.current?.focus())
    }
  }

  function preparedQuestion(): string | null {
    setQuestionError(null)
    const text = question.trim()
    if (text) return text
    setQuestionError('请输入问题。')
    questionRef.current?.focus()
    return null
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = preparedQuestion()
    if (!text) return
    let restoreButtonFocus = window.document.activeElement === askButtonRef.current
    setAsking(true)
    setResult(null)
    setAgentProgress(null)
    setAgentStopReason(null)
    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }),
      })
      const data = await jsonBody<ChatResponse>(response)
      if (data?.error_code === 'context_limit_exceeded') {
        setQuestionError(chatErrors.context_limit_exceeded)
        restoreButtonFocus = false
        questionRef.current?.focus()
      } else if (!response.ok || data?.status === 'error') {
        setResult({ kind: 'error', message: chatErrors[data?.error_code ?? ''] ?? '问答请求失败，请稍后重试。' })
      } else if (data?.status === 'insufficient_evidence') {
        setResult({ kind: 'insufficient' })
      } else if (data?.status === 'answered' && data.answer && Array.isArray(data.citations) && data.citations.length) {
        setResult({ kind: 'answered', answer: data.answer, citations: data.citations, runId: data.run_id ?? '' })
      } else {
        setResult({ kind: 'error', message: '问答响应格式异常，请稍后重试。' })
      }
    } catch {
      setResult({ kind: 'error', message: '无法连接问答服务，请检查后端是否启动。' })
    } finally {
      setAsking(false)
      if (restoreButtonFocus) requestAnimationFrame(() => askButtonRef.current?.focus())
    }
  }

  async function askAgent() {
    const text = preparedQuestion()
    if (!text) return
    const restoreFocus = window.document.activeElement === agentButtonRef.current
    const controller = new AbortController()
    agentAbortRef.current = controller
    const progress: AgentProgress = { runId: '', stages: [], text: '', citations: [] }
    setAsking(true)
    setResult(null)
    setAgentStopReason(null)
    setAgentProgress(progress)
    try {
      const response = await fetch('/api/agent/run/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }), signal: controller.signal,
      })
      await consumeAgentStream(response, (event, data) => {
        progress.runId = data.run_id as string
        if (event === 'stage') {
          if (typeof data.stage !== 'string' || typeof data.status !== 'string') throw new Error('invalid stage')
          progress.stages = [...progress.stages, data as AgentStage]
          setAgentProgress({ ...progress })
        } else if (event === 'token') {
          if (typeof data.text !== 'string') throw new Error('invalid token')
          progress.text += data.text
          setAgentProgress({ ...progress })
        } else if (event === 'citation') {
          const citation = data.citation as Citation | undefined
          if (!citation || typeof citation.chunk_id !== 'string' || typeof citation.document_id !== 'string' ||
              typeof citation.document_title !== 'string' || typeof citation.chunk_text !== 'string') throw new Error('invalid citation')
          progress.citations = [...progress.citations, citation]
          setAgentProgress({ ...progress })
        } else if (event === 'done') {
          if (typeof data.stop_reason !== 'string') throw new Error('invalid stop reason')
          if (data.status === 'answered' && typeof data.answer === 'string' && data.answer.trim() && data.answer === progress.text &&
              progress.citations.length > 0 && Array.isArray(data.citation_ids) &&
              JSON.stringify(data.citation_ids) === JSON.stringify(progress.citations.map(item => item.chunk_id))) {
            setResult({ kind: 'answered', answer: data.answer, citations: progress.citations, runId: progress.runId })
          } else if (data.status === 'insufficient_evidence' && data.answer === null && progress.citations.length === 0 &&
                     Array.isArray(data.citation_ids) && data.citation_ids.length === 0) {
            setResult({ kind: 'insufficient' })
          } else throw new Error('invalid completed run')
          setAgentStopReason(data.stop_reason)
        } else if (event === 'error') {
          progress.text = ''
          progress.citations = []
          setAgentProgress({ ...progress })
          setResult({ kind: 'error', message: chatErrors[String(data.error_code)] ?? 'Agent 运行失败，请重试。' })
        }
      })
    } catch (error) {
      progress.text = ''
      progress.citations = []
      setAgentProgress({ ...progress })
      setResult({ kind: 'error', message: error instanceof StreamInterruptedError || controller.signal.aborted
        ? '回答流连接中断，本轮未完成。请重新提问。'
        : 'Agent 回答流不可用或格式异常，请稍后重试。' })
    } finally {
      agentAbortRef.current = null
      setAsking(false)
      if (restoreFocus) requestAnimationFrame(() => agentButtonRef.current?.focus())
    }
  }

  return <div className="min-h-screen bg-[#f3f5f1] text-[#17312d]">
    <header className="border-b border-[#d9e2da] bg-white/90">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8">
        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#183b34] text-lg font-bold text-white">D</span>
          <div><p className="text-lg font-bold leading-5 tracking-tight">DocScope</p><p className="text-xs text-[#657a73]">Evidence workspace</p></div>
        </div>
        <span className="rounded-full border border-[#c8ddd0] bg-[#edf6ee] px-3 py-1.5 text-xs font-semibold tracking-wide text-[#285c41]">AGENT 预览 · DENSE 基线</span>
      </div>
    </header>

    <main className="mx-auto max-w-6xl px-5 pb-20 pt-10 sm:px-8 sm:pt-14">
      <div className="max-w-3xl">
        <p className="text-xs font-bold uppercase tracking-[0.24em] text-[#367653]">Document intelligence / 01</p>
        <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-tight text-[#15352f] sm:text-5xl">从文档出发，<br />让每个回答都有出处。</h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-[#60736b]">上传英文文本型 PDF 或 Markdown 文档，提出一个独立问题。DocScope 会检索原文证据，并展示可核对的来源片段。</p>
      </div>

      <div className="mt-10 grid gap-6 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
        <section aria-labelledby="upload-title" className="rounded-[26px] border border-[#dce5de] bg-white p-6 shadow-[0_18px_50px_-38px_#153b30] sm:p-8">
          <div className="flex items-start gap-4">
            <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#e9f4eb] text-sm font-bold text-[#2c6a46]">01</span>
            <div><h2 id="upload-title" className="text-xl font-semibold tracking-tight">导入资料</h2><p className="mt-1 text-sm leading-6 text-[#60736b]">选择文本型 PDF 或 Markdown，完成解析、分块与索引。</p></div>
          </div>
          <form onSubmit={upload} className="mt-8 space-y-5">
            <div>
              <label htmlFor="markdown-file" className="block text-sm font-semibold">PDF 或 Markdown 文件</label>
              <input ref={fileRef} id="markdown-file" type="file" accept=".pdf,application/pdf,.md,.markdown,text/markdown" onChange={event => { setFile(event.target.files?.[0] ?? null); setUploadError(null) }} aria-invalid={Boolean(uploadError)} aria-describedby={uploadError ? 'file-hint upload-error' : 'file-hint'} className={`mt-2 block w-full cursor-pointer rounded-xl border border-[#cddbd0] bg-[#f8faf7] p-2 text-sm text-[#52675d] file:mr-4 file:cursor-pointer file:rounded-lg file:border-0 file:bg-[#e4eee5] file:px-4 file:py-2 file:font-semibold file:text-[#23583a] ${focus}`} />
              <p id="file-hint" className="mt-2 text-xs leading-5 text-[#60736b]">支持文本型 PDF 或 UTF-8 Markdown · 最大 1 MiB</p>
            </div>
            <p className="text-xs leading-5 text-[#60736b]">同名文件重传会替换现有文档；同内容不会重复索引。</p>
            {replacement && <p className="text-sm">替换目标：{replacement.filename}<button type="button" disabled={uploading} onClick={() => setReplacement(null)} className={`ml-2 rounded px-2 underline ${focus}`}>取消替换</button></p>}
            <button ref={uploadButtonRef} type="submit" disabled={uploading || Boolean(deleting)} className={`w-full rounded-xl bg-[#24583b] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#19472e] disabled:cursor-wait disabled:opacity-60 ${focus}`}>{uploading ? '正在建立索引…' : replacement ? '上传并替换' : '上传并建立索引'}</button>
          </form>
          {uploading && <p role="status" className="mt-5 rounded-xl bg-[#eef5ef] px-4 py-3 text-sm text-[#285d42]">正在解析文档并建立索引，请稍候。</p>}
          {uploadError && <p id="upload-error" role="alert" className="mt-5 rounded-xl border border-[#edc8bb] bg-[#fff2ec] px-4 py-3 text-sm font-medium text-[#9a442a]">{uploadError}</p>}
          <div className="mt-6 border-t border-[#e6ece6] pt-5">
            <div className="flex items-center justify-between gap-2"><h3 ref={documentsHeadingRef} tabIndex={-1} className={`rounded text-sm font-semibold ${focus}`}>文档状态</h3><button type="button" onClick={() => { void refreshDocuments(); setCitationRefresh(value => value + 1) }} className={`rounded px-2 py-1 text-sm underline ${focus}`}>刷新文档</button></div>
            {documentsError && <p role="alert" className="mt-2 text-sm text-[#9a442a]">{documentsError}</p>}
            {operationError && <p role="alert" className="mt-2 text-sm text-[#9a442a]">{operationError}</p>}
            {!documents.length && !documentsError && <p className="mt-3 text-sm text-[#60736b]">尚未导入文档</p>}
            <ul aria-label="文档列表" className="mt-3 space-y-3">{documents.map(record => <li key={record.document_id} className="rounded-xl border border-[#d9e5dc] p-3">
              <p className="break-all text-sm font-semibold">{record.filename}</p>
              <p className="mt-1 text-sm text-[#60736b]">{documentStatuses[record.status]}{record.status === 'ready' && record.chunk_count != null ? ` · ${record.chunk_count} 个原文片段` : ''}</p>
              {record.error_code && <p className="mt-1 text-xs text-[#9a442a]">{uploadErrors[record.error_code] ?? '操作失败，请重试。'}</p>}
              <div className="mt-2 flex gap-3"><button type="button" aria-label={`替换 ${record.filename}`} disabled={uploading || Boolean(deleting) || ['processing', 'deleting'].includes(record.status)} onClick={() => { setReplacement(record); setUploadError(null); fileRef.current?.focus() }} className={`rounded px-2 py-1 text-sm underline disabled:opacity-50 ${focus}`}>替换 / 重试上传</button><button type="button" aria-label={`删除 ${record.filename}`} disabled={uploading || Boolean(deleting) || ['processing', 'deleting'].includes(record.status)} onClick={() => { void deleteDocument(record) }} className={`rounded px-2 py-1 text-sm text-[#9a442a] underline disabled:opacity-50 ${focus}`}>删除</button></div>
            </li>)}</ul>
          </div>
        </section>

        <section aria-labelledby="question-title" className="min-w-0 rounded-[26px] border border-[#dce5de] bg-white p-6 shadow-[0_18px_50px_-38px_#153b30] sm:p-8">
          <div className="flex items-start gap-4">
            <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#e9f4eb] text-sm font-bold text-[#2c6a46]">02</span>
            <div><h2 id="question-title" className="text-xl font-semibold tracking-tight">检索与问答</h2><p className="mt-1 text-sm leading-6 text-[#60736b]">每次提问独立检索，不依赖聊天历史。</p></div>
          </div>
          <form onSubmit={ask} className="mt-8 space-y-4">
            <div>
              <label htmlFor="question" className="block text-sm font-semibold">独立英文问题</label>
              <textarea ref={questionRef} id="question" value={question} onChange={event => { setQuestion(event.target.value); setQuestionError(null) }} maxLength={500} rows={3} placeholder="Which observatory built the Orion telescope?" aria-invalid={Boolean(questionError)} aria-describedby={questionError ? 'question-error' : 'question-hint'} className={`mt-2 w-full resize-y rounded-xl border border-[#cddbd0] bg-[#fbfcfa] px-4 py-3 text-sm leading-6 placeholder:text-[#60736b] ${focus}`} />
              <p id="question-hint" className="mt-1 text-xs text-[#60736b]">可选择 Dense 基线或有界 Agent 流式问答；每次提问独立运行。</p>
              {questionError && <p id="question-error" role="alert" className="mt-2 text-sm font-medium text-[#9a442a]">{questionError}</p>}
            </div>
            <div className="flex flex-wrap gap-3">
              <button ref={askButtonRef} type="submit" disabled={asking} className={`rounded-xl border border-[#173b34] px-6 py-3 text-sm font-semibold text-[#173b34] transition hover:bg-[#edf5ee] disabled:cursor-wait disabled:opacity-60 ${focus}`}>{asking ? '正在检索与回答…' : '检索并回答'}</button>
              <button ref={agentButtonRef} type="button" disabled={asking} onClick={() => { void askAgent() }} className={`rounded-xl bg-[#173b34] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[#0f2c27] disabled:cursor-wait disabled:opacity-60 ${focus}`}>Agent 流式回答</button>
            </div>
          </form>
          <div className="mt-8 border-t border-[#e6ece6] pt-7">
            <h3 className="text-xs font-bold uppercase tracking-[0.18em] text-[#52675d]">回答与证据</h3>
            {asking && <p role="status" aria-live="polite" className="mt-4 text-sm text-[#52695e]">{agentProgress?.stages.length
              ? `Agent 正在运行：${agentProgress.stages.at(-1)?.status === 'failed' ? '阶段失败' : agentProgress.stages.at(-1)?.stage === 'retrieve' ? '检索完成' : agentProgress.stages.at(-1)?.stage === 'plan_next' ? '规划下一查询' : '生成回答'}`
              : '正在检索原文并生成回答，请稍候。'}</p>}
            {asking && agentProgress?.text && <p aria-live="off" className="mt-4 text-lg leading-8 text-[#1a3b30]">临时回答，等待完成确认：{agentProgress.text}</p>}
            {!asking && !result && <p className="mt-4 text-sm leading-6 text-[#60736b]">回答和原文引用将在这里显示。</p>}
            {result?.kind === 'error' && <p role="alert" className="mt-4 rounded-xl border border-[#edc8bb] bg-[#fff2ec] px-4 py-3 text-sm font-medium text-[#9a442a]">{result.message}</p>}
            {result?.kind === 'insufficient' && <p role="status" className="mt-4 rounded-xl border border-[#d5e1dc] bg-[#f2f6f3] px-4 py-3 text-sm text-[#395d4b]">当前文档没有足够证据回答这个问题。</p>}
            {result?.kind === 'answered' && <div role="status" className="mt-4">
              <p className="text-lg font-medium leading-8 text-[#1a3b30]">{result.answer}</p>
              <h4 className="mt-5 text-sm font-semibold">引用集合</h4>
              <div className="mt-6 min-w-0 space-y-3">{result.citations.map((citation, index) => <CitationView key={`${result.runId}-${citation.chunk_id}`} citation={citation} index={index} refreshToken={citationRefresh} updating={uploading || Boolean(deleting)} focusClass={focus} />)}</div>
            </div>}
            {agentProgress && <section aria-label="Agent 执行轨迹" className="mt-6 rounded-xl border border-[#d9e5dc] bg-[#f8faf7] p-4">
              <h4 className="text-sm font-semibold">Agent 执行轨迹</h4>
              {agentProgress.runId && <p className="mt-1 break-all text-xs text-[#52675d]">Run ID：{agentProgress.runId}</p>}
              <ol className="mt-3 space-y-2 text-sm text-[#395d4b]">{agentProgress.stages.map((step, index) => <li key={index}>
                <span className="font-medium">{step.stage === 'retrieve' ? `第 ${step.round} 轮检索${step.status === 'failed' ? '失败' : '完成'}` : step.status === 'failed' ? '运行阶段失败' : step.stage === 'plan' ? '查询已确定' : step.stage === 'plan_next' ? '证据评估与下一查询' : step.stage === 'answer' ? '回答阶段完成' : '运行阶段完成'}</span>
                {(step.query || step.next_query) && <span className="block break-words">查询：{step.query ?? step.next_query}</span>}
                {step.stage === 'retrieve' && <span className="block">返回 {step.evidence_ids?.length ?? 0} 条证据 · 新增 {step.new_evidence_ids?.length ?? 0} 条</span>}
              </li>)}</ol>
              {agentStopReason && <p className="mt-3 border-t border-[#d9e5dc] pt-3 text-sm font-semibold">停止原因：{stopReasons[agentStopReason] ?? agentStopReason}</p>}
            </section>}
          </div>
        </section>
      </div>
      <p className="mt-10 text-center text-xs leading-5 text-[#60736b]">Dense 基线与有界 Agent 均可独立提问。引用可定位原文片段，仍需人工核对结论是否受证据支持。</p>
    </main>
  </div>
}
