import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError, json, readChatStream, reason, request } from './api'
import { updateAnswer } from './conversations'
import type { Draft } from './drafts'
import type { Conversation, Message } from './conversations'

type Turn = NonNullable<Draft["pending"]>
type Flight = { turn: Turn; first: boolean; controller: AbortController; accepted: () => void; stopping: boolean }

export function useConversations(token: string, kbId: string | undefined, chatId: string | undefined, archived: boolean) {
  const [listing, setListing] = useState<{ kb?: string; items: Conversation[] }>({ items: [] })
  const [messages, setMessages] = useState<Record<string, Message[]>>({})
  const [verified, setVerified] = useState<string>()
  const justAccepted = useRef<string | undefined>(undefined)
  useLayoutEffect(() => {
    setVerified(chatId === justAccepted.current ? chatId : undefined)
    justAccepted.current = undefined
  }, [chatId, kbId])
  const [warning, setWarning] = useState('')
  const [revision, setRevision] = useState(0)
  const [lifetime] = useState(() => new AbortController())
  const active = useRef(new Map<string, Flight>())
  const [, render] = useState(0)
  const epochs = useRef(new Map<string, number>())
  const chats = listing.kb === kbId ? listing.items : []
  useEffect(() => () => lifetime.abort(), [lifetime])

  useEffect(() => {
    if (!kbId) return
    const stop = new AbortController()
    void json<Conversation[]>(`/knowledge-bases/${kbId}/conversations?archived=${archived}`, token, { signal: stop.signal })
      .then((items) => {
        if (!stop.signal.aborted) setListing({ kb: kbId, items })
      })
      .catch((error) => {
        if (!stop.signal.aborted) {
          setListing({ kb: kbId, items: [] })
          setWarning(reason(error))
        }
      })
    return () => stop.abort()
  }, [kbId, token, revision, archived])

  useEffect(() => {
    if (!chatId || !kbId) return
    const stop = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let readNumber = 0
    async function refresh(id: string) {
      const read = ++readNumber
      const epoch = epochs.current.get(id)
      try {
        if (active.current.has(id)) {
          await json(`/knowledge-bases/${kbId}`, token, { signal: stop.signal })
          if (!stop.signal.aborted && read === readNumber) setVerified(id)
          return
        }
        const rows = await json<Message[]>(`/conversations/${id}/messages`, token, { signal: stop.signal })
        if (stop.signal.aborted || read !== readNumber || active.current.has(id) || epoch !== epochs.current.get(id)) return
        setVerified(id)
        setMessages((previous) => ({ ...previous, [id]: rows }))
        const last = rows.at(-1)
        if (last?.role === 'assistant' && last.status === 'streaming') timer = setTimeout(() => void refresh(id), 1200)
      } catch (error) {
        if (!stop.signal.aborted && read === readNumber) {
          setVerified(undefined)
          setWarning(reason(error))
          if (error instanceof ApiError && error.status === 404) setMessages((previous) => ({ ...previous, [id]: [] }))
        }
      }
    }
    void refresh(chatId)
    function focus() {
      setVerified(undefined)
      clearTimeout(timer)
      void refresh(chatId!)
    }
    window.addEventListener('focus', focus)
    return () => {
      stop.abort()
      clearTimeout(timer)
      window.removeEventListener('focus', focus)
    }
  }, [chatId, kbId, token, revision])

  async function send(id: string, turn: NonNullable<Draft['pending']>, first: boolean, accepted: () => void) {
    if (active.current.has(id) || !kbId) return
    const controller = new AbortController()
    const flight = { turn, first, controller, accepted, stopping: false }
    active.current.set(id, flight)
    render((n) => n + 1)
    epochs.current.set(id, (epochs.current.get(id) ?? 0) + 1)
    const { question } = turn
    let answerId = ''
    let firstVisible = true
    const submittedAt = performance.now()
    function edit(fn: (items: Message[]) => Message[]) {
      if (!controller.signal.aborted && !lifetime.signal.aborted) setMessages((previous) => ({ ...previous, [id]: fn(previous[id] ?? []) }))
    }
    try {
      setWarning('')
      const response = await request(`/conversations/${id}/messages`, token, {
        method: 'POST',
        body: JSON.stringify({ ...turn, ...(first && { first_in: kbId }) }),
        signal: AbortSignal.any([lifetime.signal, controller.signal]),
        headers: { Accept: 'text/event-stream' },
      })
      if (!response.body || !response.headers.get('content-type')?.startsWith('text/event-stream'))
        throw new Error('服务端未返回回答流。')
      await readChatStream(
        response.body,
        id,
        (text, citations) => {
          if (controller.signal.aborted) return
          if (firstVisible && text) {
            performance.measure('nabucite:first-visible-delta', { start: submittedAt, end: performance.now() })
            firstVisible = false
          }
          edit((items) => updateAnswer(items, answerId, { type: 'delta', text, citations }))
        },
        (userId, assistantId, userTime, answerTime) => {
          answerId = assistantId
          if (controller.signal.aborted || lifetime.signal.aborted) return
          if (first && window.location.hash.endsWith('/draft/' + id)) justAccepted.current = id
          accepted()
          setListing((previous) => {
            if (previous.kb !== kbId) return previous
            const chat = previous.items.find((c) => c.id === id) ?? {
              id,
              title: question.slice(0, 100),
              knowledge_base_id: kbId,
              updated_at: new Date().toISOString(),
            }
            return { ...previous, items: [chat, ...previous.items.filter((c) => c.id !== id)] }
          })
          edit((items) => {
            const next = [...items]
            if (!next.some((m) => m.id === userId)) {
              next.push({ role: 'user', id: userId, content: question, request_id: turn.request_id, mode: turn.mode, created_at: userTime })
            }
            next.push({
              role: 'assistant', id: answerId, content: '', status: 'streaming',
              request_id: turn.request_id, reply_to: userId, created_at: answerTime,
            })
            return next
          })
        },
        (citations) => edit((items) => updateAnswer(items, answerId, { type: 'done', citations })),
        (step) => edit((items) => updateAnswer(items, answerId, { type: 'progress', step })),
      )
    } catch (error) {
      if (!controller.signal.aborted && !lifetime.signal.aborted) {
        if (!answerId && window.location.hash.endsWith('/' + id)) setWarning(reason(error))
        if (error instanceof ApiError && error.status === 409 && error.code === 'request_exists') {
          accepted()
          setWarning('')
        }
        edit((items) => updateAnswer(items, answerId, { type: 'error', text: reason(error) }))
      }
    } finally {
      if (!flight.stopping && active.current.get(id) === flight) active.current.delete(id)
      if (!lifetime.signal.aborted) setRevision((n) => n + 1)
    }
  }
  async function stop(id: string, saved?: Turn) {
    const flight = active.current.get(id)
    const turn = flight?.turn ?? saved
    if (!turn || flight?.stopping) return
    if (flight) {
      flight.stopping = true
      flight.controller.abort()
    }
    epochs.current.set(id, (epochs.current.get(id) ?? 0) + 1)
    setMessages((previous) => ({
      ...previous,
      [id]: (previous[id] ?? []).map((m) =>
        m.role === 'assistant' && m.request_id === turn.request_id
          ? { ...m, status: 'withdrawn', content: '', citations: [], progress: [] }
          : m,
      ),
    }))
    try {
      await json(`/conversations/${id}/withdraw`, token, {
        method: 'POST',
        body: JSON.stringify({ ...turn, ...(flight?.first && { first_in: kbId }) }),
      })
      flight?.accepted()
    } catch (error) {
      setWarning('撤回未确认：' + reason(error))
    } finally {
      if (active.current.get(id) === flight) active.current.delete(id)
      setRevision((n) => n + 1)
    }
  }
  return {
    chats, verified, messages, send, stop, warning, setWarning,
    isActive: (id: string) => active.current.has(id),
    refresh: () => setRevision((n) => n + 1),
  }
}
