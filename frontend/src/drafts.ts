import type { Mode } from './conversations'

export interface Draft {
  id: string
  kb: string
  text: string
  mode: Mode
  formal: boolean
  pending?: { regenerate?: string; request_id: string; question: string; mode: Mode }
}
export type Drafts = Record<string, Draft>

export function readDrafts(userId: string): Drafts {
  try {
    const parsed = JSON.parse(sessionStorage.getItem('nabucite.drafts.' + userId) || '{}')
    return Object.fromEntries(
      Object.entries(parsed).filter(([id, value]) => {
        const d = value as Draft | null
        return d?.id === id && typeof d.kb === 'string' && typeof d.text === 'string' && ['general', 'knowledge'].includes(d.mode)
      }),
    ) as Drafts
  } catch {
    return {}
  }
}
export function pendingTurn(d: Draft) {
  const question = d.text.trim()
  return d.pending?.question === question && d.pending.mode === d.mode
    ? d.pending
    : { request_id: crypto.randomUUID(), question, mode: d.mode }
}
export function acceptDraft(d: Draft, requestId: string): Draft {
  if (d.pending?.request_id !== requestId) return d
  return { ...d, text: d.text.trim() === d.pending.question ? '' : d.text, formal: true, pending: undefined }
}
export function draftTitle(d: Draft) {
  return d.formal ? '' : d.text.trim().split(/\r?\n/, 1)[0].slice(0, 48)
}
