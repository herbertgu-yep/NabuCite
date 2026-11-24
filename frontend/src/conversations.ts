export interface User {
  id: string
  username: string
  display_name: string
  enabled: boolean
  system_admin: boolean
  department: Org
  projects: Org[]
}
export interface Library {
  id: string
  name: string
  role: 'admin' | 'reader'
  sources: { name: string; role: string; kind: string }[]
}
export interface Conversation {
  id: string
  knowledge_base_id: string
  title: string
  updated_at: string
}

export type Stage = 'rewrite' | 'retrieve' | 'evidence' | 'answer' | 'wait' | 'read'
export interface Progress {
  id?: string
  elapsed_ms?: number
  stage: Stage
  status: 'active' | 'complete' | 'empty' | 'failed'
  seq: number
}
export type Message = {created_at?: string; request_id?: string; reply_to?: string; mode?: Mode} & (
  | { id: string; role: 'user'; content: string; request_id?: string }
  | {
      id: string
      role: 'assistant'
      content: string
      status: 'streaming' | 'done' | 'interrupted' | 'withdrawn'
      progress?: Progress[]
      error?: string | null
      citations?: Citation[]
    })

export function updateAnswer(
  messages: Message[],
  answerId: string,
  update:
    | { type: 'progress'; step: Progress }
    | { type: 'delta' | 'error'; text: string; citations?: Citation[] }
    | { type: 'done'; citations?: Citation[] },
): Message[] {
  return messages.map((message) => {
    if (message.id !== answerId || message.role !== 'assistant' || message.status !== 'streaming') return message
    if (update.type === 'progress') {
      const steps = message.progress ?? []
      if (steps.some((s) => s.seq >= update.step.seq)) return message
      return { ...message, progress: [...steps.filter((s) => (s.id ?? s.stage) !== (update.step.id ?? update.step.stage)), update.step] }
    }
    if (update.type === 'delta')
      return { ...message, content: message.content + update.text, ...(update.citations && { citations: update.citations }) }
    if (update.type === 'error') return { ...message, status: 'interrupted', error: update.text }
    return { ...message, status: 'done', ...(update.citations && { citations: update.citations }) }
  })
}

export type Mode = 'general' | 'knowledge'
export interface Citation {
  number: number
  knowledge_base_id: string
  document_id: string
  chunk_id: string
  filename: string
  page_start: number
  page_end: number
  section: string
}
export interface Org {
  id: string
  kind: 'department' | 'project'
  name: string
}
