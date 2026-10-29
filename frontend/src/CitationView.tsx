import { useEffect, useState } from 'react'

export type Citation = {
  document_id: string
  chunk_id: string
  document_title: string
  page_number?: number | null
  section: string | null
  line_start: number | null
  chunk_text: string
}

export function CitationView({ citation, index, refreshToken, updating, focusClass }: {
  citation: Citation; index: number; refreshToken: number; updating: boolean; focusClass: string
}) {
  const [open, setOpen] = useState(false)
  const [retry, setRetry] = useState(0)
  const [check, setCheck] = useState<{ token: number; status: 'checking' | 'valid' | 'invalid' | 'error' }>({ token: -1, status: 'checking' })
  const status = updating || check.token !== refreshToken ? 'checking' : check.status

  useEffect(() => {
    if (!open || updating) return
    const controller = new AbortController()
    setCheck({ token: refreshToken, status: 'checking' })
    async function verify() {
      try {
        const response = await fetch(`/api/documents/${encodeURIComponent(citation.document_id)}/citations/${encodeURIComponent(citation.chunk_id)}`, { signal: controller.signal })
        const data = await response.json()
        if (!response.ok || !['valid', 'invalid'].includes(data.status)) throw new Error('cannot verify')
        if (!controller.signal.aborted) setCheck({ token: refreshToken, status: data.status })
      } catch {
        if (!controller.signal.aborted) setCheck({ token: refreshToken, status: 'error' })
      }
    }
    void verify()
    return () => controller.abort()
  }, [citation.document_id, citation.chunk_id, open, refreshToken, retry, updating])

  const page = citation.page_number != null ? `第 ${citation.page_number} 页` : null
  return <details onToggle={event => {
    setCheck({ token: refreshToken, status: 'checking' })
    setOpen(event.currentTarget.open)
  }} className="group min-w-0 rounded-xl border border-[#d9e5dc] bg-[#f8faf7] [overflow-wrap:anywhere]">
    <summary className={`cursor-pointer list-none rounded-xl px-4 py-3 text-sm font-semibold text-[#24583b] ${focusClass}`}>查看来源 {index + 1} · {citation.document_title} · {page ?? citation.section}<span aria-hidden="true" className="float-right text-[#648975] group-open:rotate-90">→</span></summary>
    <div className="border-t border-[#dfe9e1] px-4 pb-4 pt-3">
      {status === 'checking' && <p className="text-sm text-[#60736b]">正在核验引用…</p>}
      {status === 'invalid' && <p className="text-sm font-medium text-[#9a442a]">引用已失效：来源已替换、删除或不可用。请重新提问获取当前证据。</p>}
      {status === 'error' && <div><p className="text-sm text-[#9a442a]">暂时无法核验引用，请稍后重试。</p><button onClick={() => setRetry(value => value + 1)} className={`mt-2 rounded px-2 py-1 text-sm underline ${focusClass}`}>重新核验引用</button></div>}
      {status === 'valid' && <><p className="break-all text-xs font-semibold text-[#425c50]">{citation.document_title}</p><p className="mt-1 text-xs text-[#60736b]">{page ?? `${citation.section} · 第 ${citation.line_start} 行`}</p><blockquote className="mt-3 border-l-2 border-[#74a783] pl-3 text-sm leading-6 text-[#243f32]">{citation.chunk_text}</blockquote></>}
    </div>
  </details>
}
