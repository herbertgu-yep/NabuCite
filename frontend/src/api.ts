import type { Citation, Progress } from './conversations'
export const tokenKey = 'nabucite.auth.token'

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function request(path: string, token: string | null, init: RequestInit = {}) {
  const base = import.meta.env?.DEV ? '' : (import.meta.env?.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '')
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(base + '/api' + path, { ...init, headers })
  } catch (error) {
    if (init.signal?.aborted) throw error
    throw new Error('无法连接 NabuCite 后端，请检查服务及网络。')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    if (response.status === 401 && token && sessionStorage.getItem(tokenKey) === token) {
      window.dispatchEvent(new Event('nabucite:expired'))
    }
    const detail = body?.detail
    const reason =
      response.status === 401
        ? token
          ? '登录已失效，请重新登录。'
          : '用户名或密码不正确。'
        : response.status === 403
          ? typeof detail === 'string'
            ? detail
            : '当前操作权限不足。'
          : typeof detail?.message === 'string'
            ? detail.message
            : typeof detail === 'string'
              ? detail
              : `请求失败（HTTP ${response.status}）。`
    throw new ApiError(response.status, reason, detail?.code)
  }
  return response
}

export async function json<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  return (await request(path, token, init)).json()
}

export function reason(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

/** 分别按 UTF-8 字节、SSE 空行解码；只有 done 事件能确认回答完整。 */
export async function readChatStream(
  stream: ReadableStream<Uint8Array>,
  chatId: string,
  onDelta: (text: string, citations?: Citation[]) => void,
  onStart?: (userId: string, answerId: string, userTime?: string, answerTime?: string) => void,
  onDone?: (citations: Citation[]) => void,
  onProgress?: (step: Progress) => void,
) {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = ''
  let previousCR = false
  let answerId: string | undefined
  try {
    while (true) {
      const { value, done } = await reader.read()
      let chunk = decoder.decode(value, { stream: !done })
      if (chunk) {
        if (previousCR && chunk.startsWith('\n')) chunk = chunk.slice(1)
        previousCR = chunk.endsWith('\r')
        buffer += chunk.replace(/\r\n?/g, '\n')
      }
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const lines = buffer.slice(0, boundary).split('\n')
        buffer = buffer.slice(boundary + 2)
        let event = ''
        const data = []
        for (const line of lines) {
          const colon = line.indexOf(':')
          const name = colon < 0 ? line : line.slice(0, colon)
          const field = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
          if (name === 'event') event = field
          if (name === 'data') data.push(field)
        }
        if (!['start', 'progress', 'delta', 'done', 'error'].includes(event)) continue
        const body = JSON.parse(data.join('\n'))
        if (body?.conversation_id !== chatId) throw new Error('回答的会话 ID 不匹配。')
        if (event === 'start') {
          if (typeof body.user_message_id !== 'string' || typeof body.answer_id !== 'string') throw new Error('回答 ID 格式异常。')
          if (answerId) throw new Error('回答流重复开始。')
          answerId = body.answer_id
          if (body.user_time || body.answer_time) onStart?.(body.user_message_id, body.answer_id, body.user_time, body.answer_time)
          else onStart?.(body.user_message_id, body.answer_id)
          continue
        }
        if (onStart && (!answerId || answerId !== body.answer_id)) throw new Error('回答 ID 不匹配。')
        if (event === 'progress') {
          if (
            !answerId ||
            body.answer_id !== answerId ||
            !Number.isSafeInteger(body.seq) ||
            body.seq < 1 ||
            !['rewrite', 'retrieve', 'evidence', 'answer', 'wait', 'read'].includes(body.stage) ||
            !['active', 'complete', 'failed', 'empty'].includes(body.status)
          )
            throw new Error('回答进度格式异常。')
          onProgress?.({ ...(typeof body.id === 'string' && {id:body.id, elapsed_ms:body.elapsed_ms}), stage: body.stage, status: body.status, seq: body.seq })
          continue
        }
        if (
          body.citations !== undefined &&
          (!Array.isArray(body.citations) ||
            !body.citations.every(
              (c: Citation) =>
                c &&
                Number.isInteger(c.number) &&
                typeof c.document_id === 'string' &&
                typeof c.knowledge_base_id === 'string' &&
                typeof c.chunk_id === 'string' &&
                Number.isInteger(c.page_start),
            ))
        )
          throw new Error('回答引用格式异常。')
        if (event === 'done') {
          onDone?.(body.citations ?? [])
          return
        }
        if (event === 'error') throw new Error(typeof body.message === 'string' ? body.message : '模型调用失败。')
        if (typeof body.text !== 'string') throw new Error('回答增量格式异常。')
        onDelta(body.text, body.citations)
      }
      if (done) throw new Error('连接提前关闭，未收到完成信号。')
    }
  } catch (error) {
    if (error instanceof TypeError || error instanceof SyntaxError) throw new Error('回答流中断或格式异常。')
    throw error
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
