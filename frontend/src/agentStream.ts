export type StreamEvent = 'stage' | 'token' | 'citation' | 'done' | 'error'

export class StreamInterruptedError extends Error {}

export async function consumeAgentStream(
  response: Response,
  onEvent: (event: StreamEvent, data: Record<string, unknown>) => void,
): Promise<void> {
  if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('invalid stream response')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let identity: string | null = null
  let terminal = false
  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try { chunk = await reader.read() }
      catch {
        if (terminal) return
        throw new StreamInterruptedError('stream connection failed before terminal event')
      }
      const { value, done } = chunk
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let end = buffer.indexOf('\n\n')
      while (end >= 0) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const lines = block.split('\n')
        const event = lines.find(line => line.startsWith('event: '))?.slice(7) as StreamEvent | undefined
        const raw = lines.filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n')
        if (!event || !['stage', 'token', 'citation', 'done', 'error'].includes(event) || terminal) {
          throw new Error('invalid stream event')
        }
        const data: unknown = JSON.parse(raw)
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid stream data')
        const payload = data as Record<string, unknown>
        if (typeof payload.run_id !== 'string' || !payload.run_id ||
            typeof payload.message_id !== 'string' || !payload.message_id) throw new Error('missing stream identity')
        const nextIdentity = `${payload.run_id}:${payload.message_id}`
        if (identity && identity !== nextIdentity) throw new Error('stream identity changed')
        identity = nextIdentity
        onEvent(event, payload)
        if (event === 'done' || event === 'error') terminal = true
        end = buffer.indexOf('\n\n')
      }
    }
    if (!terminal || buffer.trim()) throw new StreamInterruptedError('stream ended without a valid terminal event')
  } finally {
    reader.releaseLock()
  }
}
