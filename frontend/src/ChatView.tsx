import { UserAvatar } from './UserAvatar'
import { useEffect, useRef, useState } from 'react'
import { App, Button, Dropdown, Input, Select } from 'antd'
import { Icon } from './Icon'
import type { Library, Message, Mode, Citation } from './conversations'

import { Source } from './Source'
import { AnswerText } from './AnswerText'

const stageNames = { rewrite: '补全追问', retrieve: '检索知识库', evidence: '整理依据', answer: '输出回答',wait:'等待模型响应',read:'补读原文' }
type Answer = Extract<Message, { role: 'assistant' }>

function ProgressSteps({ answer }: { answer: Answer }) {
  const [expanded, setExpanded] = useState(false)
  const steps = answer.progress
  if (!steps?.length)
    return answer.status === 'streaming' ? (
      <p role="status" className="answer-status">
        正在生成…
      </p>
    ) : null
  const compact = answer.status === 'done' || (answer.status === 'streaming' && !!answer.content)
  const open = !compact || expanded
  return (
    <div className="progress-steps">
      {compact && (
        <button aria-expanded={open} onClick={() => setExpanded(!expanded)}>
          {answer.status === 'streaming' ? <i className="spinner" /> : <Icon name="check" />}
          {answer.status === 'streaming' ? '正在输出回答' : steps.at(-1)?.status === 'empty' ? '未找到足够依据' : '处理完成'} · {steps.length} 个步骤 <Icon name="chevron" />
        </button>
      )}
      {open && (
        <ol aria-live="polite" aria-label="执行进度">
          {steps.map((s) => {
            const status = s.status === 'active' && answer.status === 'interrupted' ? 'failed' : s.status
            return (
              <li key={s.id ?? s.stage} data-stage={s.stage} data-status={status}>
                {status === 'active' ? <i className="spinner" /> : status === 'failed' ? <b>!</b> : <Icon name="check" />}
                {status === 'empty'
                  ? '未找到足够依据'
                  : (status === 'active' ? '正在' : '') + stageNames[s.stage] + (status === 'failed' ? '已中断' : '')}
                {s.elapsed_ms !== undefined && status !== 'active' && <small> {(s.elapsed_ms/1000).toFixed(2)} 秒</small>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

function MessageTime({ value }: { value?: string }) {
  if (!value) return null
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return null
  const sameDay = date.toDateString() === new Date().toDateString()
  const full = date.toLocaleString('zh-CN')
  return <time dateTime={value} title={full} aria-label={full}>{date.toLocaleString('zh-CN', {
    ...(sameDay ? {} : {year:'numeric',month:'2-digit',day:'2-digit'}),hour:'2-digit',minute:'2-digit',hour12:false
  })}</time>
}

function Reply({ answer, token }: { token: string; answer: Extract<Message, { role: 'assistant' }> }) {
  const { message } = App.useApp()
  const [source, setSource] = useState<Citation>()
  async function copy() {
    try {
      await navigator.clipboard.writeText(answer.content)
      void message.success('已复制回答')
    } catch {
      void message.error('剪贴板不可用，请选中回答文本复制。')
    }
  }
  return (
    <article className="message-answer">
      <span className="assistant-icon">
        <Icon name="sparkles" />
      </span>
      <div>
        <div className="message-meta">NabuCite 助手 <MessageTime value={answer.created_at}/></div>
        <ProgressSteps answer={answer} />
        <AnswerText text={answer.content} citations={answer.citations ?? []} open={setSource} />
        {source && (
          <Source
            token={token}
            kb={source.knowledge_base_id}
            document={source.document_id}
            filename={source.filename}
            citation={source}
            close={() => setSource(undefined)}
          />
        )}
        {answer.status === 'interrupted' && (
          <p className="answer-status error" role="alert">
            {answer.content ? '回答已中断' : '未收到回答'} · {answer.error}
          </p>
        )}
        {answer.content && answer.status !== 'streaming' && (
          <div className="message-tools">
            <button className="copy-action" onClick={copy} aria-label="复制回答">
              <Icon name="copy" />
              复制
            </button>
          </div>
        )}
      </div>
    </article>
  )
}

export function ChatView({
  token,
  mode,
  setMode,
  draft,
  setDraft,
  messages,
  libraries,
  kbId,
  selectLibrary,
  disabled,
  onSend,
  onStop,
  onRegenerate,
  active,
  userName,
}: {
  token: string
  mode: Mode
  setMode: (v: Mode) => void
  messages: Message[]
  libraries: Library[]
  kbId?: string
  selectLibrary: (id: string) => void
  draft: string
  setDraft: (v: string) => void
  disabled: boolean
  onSend: () => Promise<void>
  onStop: () => void
  onRegenerate: (id: string) => void
  active: boolean
  userName: string
}) {
  const [sending, setSending] = useState(false)
  const scrollArea = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const last = messages.at(-1)
  const busy = active || last?.role === 'assistant' && last.status === 'streaming'

  // Regenerated attempts belong beneath the original question, even after later turns.
  const latestAnswer = new Map<string, Answer>()
  for (const item of messages) {
    if (item.role === 'assistant' && item.reply_to) latestAnswer.set(item.reply_to, item)
  }
  const visibleMessages = messages.flatMap((item): Message[] => {
    if (item.role === 'assistant') return item.reply_to ? [] : [item]
    const answer = latestAnswer.get(item.id)
    return answer ? [item, answer] : [item]
  })

  useEffect(() => {
    const area = scrollArea.current
    if (area && follow.current) area.scrollTop = area.scrollHeight
  }, [messages])

  async function submit() {
    if (disabled || sending || !draft.trim() || busy) return
    follow.current = true
    setSending(true)
    try {
      await onSend()
    } finally {
      setSending(false)
    }
  }

  return (
    <section className={`conversation${last ? '' : ' is-empty'}`} aria-label="对话">
      <div
        className="messages"
        ref={scrollArea}
        onScroll={(event) => {
          const area = event.currentTarget
          follow.current = area.scrollHeight - area.scrollTop - area.clientHeight < 60
        }}
      >
        <div className="message-column">
          {last ? (
            visibleMessages.map((item) =>
              item.role === 'user' ? (
                <article key={item.id} className="message-user">
                  <div><p>{item.content}</p><MessageTime value={item.created_at}/></div>
                  <UserAvatar name={userName} />
                </article>
              ) : item.status === 'withdrawn' ? (
                <p key={item.id} className="answer-status">回答已撤回 <Button type="link" disabled={busy || disabled} onClick={()=>item.reply_to && onRegenerate(item.reply_to)}>重新生成</Button></p>
              ) : (
                <Reply token={token} key={item.id} answer={item} />
              ),
            )
          ) : (
            <div className="welcome">
              <span className="welcome-icon">
                <Icon name="layers" />
              </span>
              <h1 className="empty-chat">今天想了解什么？</h1>
            </div>
          )}
        </div>
      </div>
      <div className="composer-wrap">
        <Dropdown
          trigger={['click']}
          disabled={busy || sending}
          autoFocus
          menu={{
            selectable: true,
            selectedKeys: [mode],
            items: (
              [
                ['knowledge', '知识库问答', 'book'],
                ['general', '通用问答', 'messages'],
              ] as const
            ).map(([key, label, icon]) => ({
              key,
              icon: <Icon name={icon} />,
              label: (
                <span className="mode-option">
                  {label}
                  {mode === key && <Icon name="check" />}
                </span>
              ),
            })),
            onClick: ({ key }) => setMode(key as Mode),
          }}
        >
          <Button
            type="text"
            aria-label="问答模式"
            className="mode-button"
            icon={<Icon name={mode === 'knowledge' ? 'book' : 'messages'} />}
          >
            {mode === 'knowledge' ? '知识库问答' : '通用问答'} <Icon name="chevron" />
          </Button>
        </Dropdown>
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <Input.TextArea
            aria-label="输入问题"
            placeholder="输入问题…"
            variant="borderless"
            autoSize={{ minRows: 1, maxRows: 6 }}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onPressEnter={(event) => {
              if (event.shiftKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
              event.preventDefault()
              submit()
            }}
          />
          <div className="composer-actions">
            {mode === 'knowledge' && <Select disabled={busy || sending}
              prefix={<Icon name="book" />}
              aria-label="选择知识库"
              placeholder="选择知识库"
              className="kb-picker"
              value={kbId}
              onChange={selectLibrary}
              variant="borderless"
              options={libraries.map((kb) => ({ value: kb.id, label: kb.name }))}
            />}
            {busy || sending ? <button key="stop" type="button" className="send-button" aria-label="停止并撤回本次回答" title="停止并撤回本次回答" onClick={onStop}>■</button> :
              <button key="send" className="send-button" type="submit" aria-label="发送" title="发送" disabled={disabled || !draft.trim()}>↑</button>}
          </div>
        </form>
        {mode === 'knowledge' && <p className="composer-note">检索当前知识库，可从回答引用查看原文</p>}
      </div>
    </section>
  )
}
