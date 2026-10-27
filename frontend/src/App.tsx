import { useRef, useState, type FormEvent } from 'react'

type Citation = {
  document_id: string
  chunk_id: string
  document_title: string
  page_number?: number | null
  section: string | null
  line_start: number | null
  chunk_text: string
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

const uploadErrors: Record<string, string> = {
  parse_failed: '文档索引失败：没有可检索的正文。请检查文件内容。',
  embedding_failed: '文档索引失败：本地向量模型未完成处理，请重试。',
  index_failed: '文档索引失败：检索索引不可用，请重试。',
  storage_failed: '文档索引失败：无法保存源文件。',
  state_failed: '文档索引失败：无法保存导入状态。',
}
const chatErrors: Record<string, string> = {
  model_failed: '模型服务调用失败，请稍后重试。',
  model_output_invalid: '模型返回了无法验证的结果，请重试。',
  retrieval_failed: '检索服务暂时不可用，请稍后重试。',
  context_limit_exceeded: '问题和证据超出本轮上下文限制，请缩短问题。',
}
const focus = 'focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-[#317a53]'

async function jsonBody<T>(response: Response): Promise<T | null> {
  try { return await response.json() as T } catch { return null }
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [document, setDocument] = useState<{ name: string; chunks: number } | null>(null)
  const [question, setQuestion] = useState('')
  const [questionError, setQuestionError] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<ChatResult | null>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const uploadButtonRef = useRef<HTMLButtonElement>(null)
  const askButtonRef = useRef<HTMLButtonElement>(null)

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setUploadError(null)
    setQuestionError(null)
    if (!file) { setUploadError('请选择 PDF 或 Markdown 文件。'); return }
    const restoreButtonFocus = window.document.activeElement === uploadButtonRef.current
    setUploading(true)
    setResult(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch('/api/documents', { method: 'POST', body })
      const data = await jsonBody<UploadResponse>(response)
      if (response.ok && data?.status === 'ready' && data.filename && data.document_id) {
        setDocument({ name: data.filename, chunks: data.chunk_count ?? 0 })
      } else if (data?.status === 'failed') {
        setUploadError(data.error_code === 'parse_failed' && file.name.toLowerCase().endsWith('.pdf')
          ? 'PDF 导入失败：无法解析可检索文本。请检查是否为扫描件、损坏文件或文档过长。'
          : uploadErrors[data.error_code ?? ''] ?? '文档索引失败，请稍后重试。')
      } else if (response.status === 413) {
        setUploadError('文件超过 1 MiB 上限，请选择更小的文件。')
      } else if (response.status === 415) {
        setUploadError('仅支持 PDF 或 Markdown 文件。')
      } else {
        setUploadError('文件无效或上传失败，请检查内容后重试。')
      }
    } catch {
      setUploadError('无法连接文档服务，请检查后端是否启动。')
    } finally {
      setUploading(false)
      if (restoreButtonFocus) requestAnimationFrame(() => uploadButtonRef.current?.focus())
    }
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setQuestionError(null)
    const text = question.trim()
    if (!text) {
      setQuestionError('请输入问题。')
      questionRef.current?.focus()
      return
    }
    let restoreButtonFocus = window.document.activeElement === askButtonRef.current
    setAsking(true)
    setResult(null)
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

  return <div className="min-h-screen bg-[#f3f5f1] text-[#17312d]">
    <header className="border-b border-[#d9e2da] bg-white/90">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8">
        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#183b34] text-lg font-bold text-white">D</span>
          <div><p className="text-lg font-bold leading-5 tracking-tight">DocScope</p><p className="text-xs text-[#657a73]">Evidence workspace</p></div>
        </div>
        <span className="rounded-full border border-[#c8ddd0] bg-[#edf6ee] px-3 py-1.5 text-xs font-semibold tracking-wide text-[#285c41]">M1 · DENSE BASELINE</span>
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
              <input id="markdown-file" type="file" accept=".pdf,application/pdf,.md,.markdown,text/markdown" onChange={event => { setFile(event.target.files?.[0] ?? null); setUploadError(null) }} aria-invalid={Boolean(uploadError)} aria-describedby={uploadError ? 'file-hint upload-error' : 'file-hint'} className={`mt-2 block w-full cursor-pointer rounded-xl border border-[#cddbd0] bg-[#f8faf7] p-2 text-sm text-[#52675d] file:mr-4 file:cursor-pointer file:rounded-lg file:border-0 file:bg-[#e4eee5] file:px-4 file:py-2 file:font-semibold file:text-[#23583a] ${focus}`} />
              <p id="file-hint" className="mt-2 text-xs leading-5 text-[#60736b]">支持文本型 PDF 或 UTF-8 Markdown · 最大 1 MiB</p>
            </div>
            <button ref={uploadButtonRef} type="submit" disabled={uploading} className={`w-full rounded-xl bg-[#24583b] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#19472e] disabled:cursor-wait disabled:opacity-60 ${focus}`}>{uploading ? '正在建立索引…' : '上传并建立索引'}</button>
          </form>
          {uploading && <p role="status" className="mt-5 rounded-xl bg-[#eef5ef] px-4 py-3 text-sm text-[#285d42]">正在解析文档并建立索引，请稍候。</p>}
          {uploadError && <p id="upload-error" role="alert" className="mt-5 rounded-xl border border-[#edc8bb] bg-[#fff2ec] px-4 py-3 text-sm font-medium text-[#9a442a]">{uploadError}</p>}
          {document && !uploading && <div role="status" className="mt-6 border-t border-[#e6ece6] pt-5"><p className="text-xs font-bold uppercase tracking-widest text-[#367653]">{uploadError ? '此前已索引' : '索引就绪'}</p><p className="mt-2 break-all font-semibold">{document.name}</p><p className="mt-1 text-sm text-[#60736b]">{document.chunks} 个原文片段可用于检索</p></div>}
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
              <p id="question-hint" className="mt-1 text-xs text-[#60736b]">当前版本使用单轮 Dense 检索。</p>
              {questionError && <p id="question-error" role="alert" className="mt-2 text-sm font-medium text-[#9a442a]">{questionError}</p>}
            </div>
            <button ref={askButtonRef} type="submit" disabled={asking} className={`rounded-xl bg-[#173b34] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[#0f2c27] disabled:cursor-wait disabled:opacity-60 ${focus}`}>{asking ? '正在检索与回答…' : '检索并回答'}</button>
          </form>
          <div className="mt-8 border-t border-[#e6ece6] pt-7">
            <h3 className="text-xs font-bold uppercase tracking-[0.18em] text-[#52675d]">回答与证据</h3>
            {asking && <p role="status" className="mt-4 text-sm text-[#52695e]">正在检索原文并生成回答，请稍候。</p>}
            {!asking && !result && <p className="mt-4 text-sm leading-6 text-[#60736b]">回答和原文引用将在这里显示。</p>}
            {result?.kind === 'error' && <p role="alert" className="mt-4 rounded-xl border border-[#edc8bb] bg-[#fff2ec] px-4 py-3 text-sm font-medium text-[#9a442a]">{result.message}</p>}
            {result?.kind === 'insufficient' && <p role="status" className="mt-4 rounded-xl border border-[#d5e1dc] bg-[#f2f6f3] px-4 py-3 text-sm text-[#395d4b]">当前文档没有足够证据回答这个问题。</p>}
            {result?.kind === 'answered' && <div role="status" className="mt-4">
              <p className="text-lg font-medium leading-8 text-[#1a3b30]">{result.answer}</p>
              <div className="mt-6 min-w-0 space-y-3">{result.citations.map((citation, index) => <details key={`${result.runId}-${citation.chunk_id}`} className="group min-w-0 rounded-xl border border-[#d9e5dc] bg-[#f8faf7] [overflow-wrap:anywhere]">
                <summary className={`cursor-pointer list-none rounded-xl px-4 py-3 text-sm font-semibold text-[#24583b] ${focus}`}>查看来源 {index + 1} · {citation.document_title} · {citation.page_number != null ? `第 ${citation.page_number} 页` : citation.section}<span aria-hidden="true" className="float-right text-[#648975] group-open:rotate-90">→</span></summary>
                <div className="border-t border-[#dfe9e1] px-4 pb-4 pt-3"><p className="break-all text-xs font-semibold text-[#425c50]">{citation.document_title}</p><p className="mt-1 text-xs text-[#60736b]">{citation.page_number != null ? `第 ${citation.page_number} 页` : `${citation.section} · 第 ${citation.line_start} 行`}</p><blockquote className="mt-3 border-l-2 border-[#74a783] pl-3 text-sm leading-6 text-[#243f32]">{citation.chunk_text}</blockquote></div>
              </details>)}</div>
            </div>}
          </div>
        </section>
      </div>
      <p className="mt-10 text-center text-xs leading-5 text-[#60736b]">当前版本展示单轮 Dense 基线。引用可定位原文片段，仍需人工核对结论是否受证据支持。</p>
    </main>
  </div>
}
