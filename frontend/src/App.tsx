import { UserAvatar } from './UserAvatar'
import { useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Button, Dropdown, Input, Modal, Switch } from 'antd'
import { Navigate, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { Administration } from './Administration'
import { Usage } from './Usage'
import { readDrafts, draftTitle, acceptDraft, pendingTurn } from './drafts'
import type { Draft } from './drafts'
import { ChatView } from './ChatView'
import { Profile } from './Profile'
import { Documents } from './Documents'
import { Icon } from './Icon'
import { Login } from './Login'
import { json, reason, tokenKey } from './api'
import type { Library, User } from './conversations'
import { useConversations } from './useConversations'

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(tokenKey))
  const [notice, setNotice] = useState('')
  const navigate = useNavigate()
  function logout() {
    sessionStorage.removeItem(tokenKey)
    setToken(null)
    void navigate('/', { replace: true })
  }
  useEffect(() => {
    function expired() {
      setNotice('登录已失效，请重新登录。')
      logout()
    }
    window.addEventListener('nabucite:expired', expired)
    return () => window.removeEventListener('nabucite:expired', expired)
  }, [navigate])
  if (!token)
    return (
      <Login
        notice={notice}
        onLogin={(value) => {
          sessionStorage.setItem(tokenKey, value)
          setNotice('')
          setToken(value)
        }}
      />
    )
  return <Workspace key={token} token={token} logout={logout} />
}

type WorkspaceProps = { token: string; logout: () => void }

function Workspace({ token, logout }: WorkspaceProps) {
  const [user, setUser] = useState<User>()
  const [libraries, setLibraries] = useState<Library[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    const stop = new AbortController()
    async function load() {
      try {
        const [me, libs] = await Promise.all([
          json<User>('/users/me', token, { signal: stop.signal }),
          json<Library[]>('/knowledge-bases', token, { signal: stop.signal }),
        ])
        if (!stop.signal.aborted) {
          setUser(me)
          setLibraries(libs)
          setError('')
        }
      } catch (e) {
        if (!stop.signal.aborted) setError(reason(e))
      }
    }
    void load()
    window.addEventListener('focus', load)
    window.addEventListener('nabucite:libraries', load)
    return () => {
      stop.abort()
      window.removeEventListener('focus', load)
      window.removeEventListener('nabucite:libraries', load)
    }
  }, [token])
  if (error || !user)
    return (
      <div className="page" role="status">
        {error || '正在读取工作台…'}
      </div>
    )
  return <Desk token={token} logout={logout} user={user} update={setUser} libraries={libraries} />
}

function Desk({
  token,
  logout,
  user,
  update,
  libraries,
}: WorkspaceProps & { user: User; update: (u: User) => void; libraries: Library[] }) {
  const navigate = useNavigate()
  const location = useLocation()
  const path = location.pathname
  const route = useMatch('/kb/:kbId/*')
  const chatId = useMatch('/kb/:kbId/chat/:id')?.params.id
  const draftId = useMatch('/kb/:kbId/draft/:id')?.params.id
  const admin = useMatch('/admin/:section')
  const docs = path.endsWith('/documents')
  const [lastKb, setLastKb] = useState(route?.params.kbId)
  const kb = libraries.find((k) => k.id === (route?.params.kbId || lastKb)) ?? libraries[0]
  useEffect(() => {
    if (route?.params.kbId) setLastKb(route.params.kbId)
  }, [route?.params.kbId])
  const home = kb ? '/kb/' + kb.id : '/'
  const blank = useMemo(() => crypto.randomUUID(), [location.key])
  const id = chatId ?? draftId ?? blank
  const [drafts, setDrafts] = useState(() => readDrafts(user.id))
  const latest = useRef(drafts)
  const [menu, setMenu] = useState(false)
  const [archived, setArchived] = useState(false)
  const [search, setSearch] = useState('')
  const [rename, setRename] = useState<{ id: string; title: string }>()
  const { chats, verified, messages, send, stop, isActive, refresh, warning, setWarning } = useConversations(token, kb?.id, chatId, archived)
  const current = chats.find((c) => c.id === id)
  const d: Draft =
    drafts[id]?.kb === kb?.id && drafts[id]
      ? drafts[id]
      : { id, kb: kb?.id ?? '', text: '', mode: 'knowledge', formal: Boolean(chatId) }

  function store(next: Draft) {
    const all = { ...latest.current, [next.id]: next }
    for (const item of Object.values(all)) {
      if (item.id !== next.id && !item.text.trim() && !item.pending && !item.formal) delete all[item.id]
    }
    latest.current = all
    setDrafts(all)
    try {
      sessionStorage.setItem('nabucite.drafts.' + user.id, JSON.stringify(all))
    } catch {
      setWarning('浏览器无法保存草稿，刷新前请保留输入。')
    }
  }
  function go(to: string) {
    setMenu(false)
    void navigate(to)
  }
  function newChat() {
    setArchived(false)
    setWarning('')
    const next = crypto.randomUUID()
    // 切换编辑器与点击同步，连续输入不会落在前一条草稿。
    flushSync(() => go(home + '/draft/' + next))
  }
  function changeDraft(change: Partial<Draft>) {
    store({ ...d, ...change })
    if (!chatId && !draftId) void navigate(home + '/draft/' + id, { replace: true })
  }
  function acknowledge(saved: Draft, requestId: string) {
    const entry = latest.current[saved.id]
    if (entry) store(acceptDraft(entry, requestId))
    if (window.location.hash === '#/kb/' + saved.kb + '/draft/' + saved.id)
      void navigate('/kb/' + saved.kb + '/chat/' + saved.id, { replace: true })
  }
  useEffect(() => {
    // 入场回执丢失时，用正式历史中的请求 ID 确认首问。
    if (draftId && current) {
      void navigate(home + '/chat/' + id, { replace: true })
    } else if (chatId && d.pending && messages[id]?.some((m) => m.role === 'user' && m.request_id === d.pending?.request_id)) {
      acknowledge(d, d.pending.request_id)
    }
  }, [draftId, current, chatId, messages, id, d.pending, home])
  async function submit() {
    if (!d.text.trim() || !kb) return
    const turn = pendingTurn(d)
    store({ ...d, pending: turn })
    if (!chatId && !draftId) void navigate(home + '/draft/' + id, { replace: true })
    await send(id, turn, !chatId, () => acknowledge(d, turn.request_id))
  }
  async function editChat(target: string, body: { title?: string; archived?: boolean }) {
    try {
      await json('/conversations/' + target, token, { method: 'PATCH', body: JSON.stringify(body) })
      setRename(undefined)
      refresh()
      if (target === chatId && body.archived !== undefined) go(home)
    } catch (e) {
      setWarning(reason(e))
    }
  }
  if (route && kb && kb.id !== route.params.kbId) return <Navigate to={home} replace />
  if (kb && (path === '/' || path === home)) return <Navigate to={home + '/draft/' + blank} replace />
  const personal = path === '/profile',
    usage = path === '/usage'
  const chatting = !docs && !personal && !usage && !admin
  const links = [
    { name: '问答工作台', icon: 'messages', to: home, selected: chatting },
    { name: '文档管理', icon: 'files', to: kb ? home + '/documents' : '/documents', selected: docs },
    { name: '个人中心', icon: 'user', to: '/profile', selected: personal },
    { name: '使用情况', icon: 'chart', to: '/usage', selected: usage },
    ...(user.system_admin ? [{ name: '管理台', icon: 'settings' as const, to: '/admin/people', selected: Boolean(admin) }] : []),
  ] as const
  const entries = [
    ...Object.values(drafts)
      .filter((v) => v.kb === kb?.id && !archived && draftTitle(v) && !chats.some((c) => c.id === v.id))
      .map((v) => ({ id: v.id, title: draftTitle(v), draft: true })),
    ...chats.map((c) => ({ ...c, draft: false })),
  ].filter((c) => c.title.toLowerCase().includes(search.trim().toLowerCase()))
  return (
    <div className="shell">
      <a
        className="skip-link"
        href="#workspace"
        onClick={(e) => {
          e.preventDefault()
          document.getElementById('workspace')?.focus()
        }}
      >
        跳转到工作区
      </a>
      <aside id="sidebar" className={'sidebar' + (menu ? ' open' : '')}>
        <button className="brand" onClick={() => go(home)}>
          <span className="brand-mark">
            <Icon name="layers" />
          </span>
          <strong>NabuCite</strong>
        </button>
        <div className="sidebar-body">
          <Button className="new-chat" type="primary" icon={<Icon name="plus" />} disabled={!kb} onClick={newChat}>
            新建对话
          </Button>
          <nav className="nav" aria-label="主导航">
            {links.map((link) => (
              <button key={link.name} aria-current={link.selected ? 'page' : undefined} onClick={() => go(link.to)}>
                <Icon name={link.icon} />
                {link.name}
              </button>
            ))}
          </nav>
          <div className="history-title">
            <span>会话历史</span>
            <label>
              <Switch
                size="small"
                checked={archived}
                onChange={(v) => {
                  setArchived(v)
                  go(home)
                }}
              />{' '}
              已归档
            </label>
          </div>
          <Input
            aria-label="搜索会话"
            prefix={<Icon name="search" />}
            placeholder="搜索会话"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <nav className="history" aria-label="最近对话">
            {!entries.length && <p className="empty-small">{search ? '没有匹配的会话' : '暂无对话'}</p>}
            {entries.map((c) => (
              <div key={c.id} className={'history-item' + (id === c.id && chatting ? ' selected' : '')}>
                <button title={c.title} onClick={() => go(home + (c.draft ? '/draft/' : '/chat/') + c.id)}>
                  {c.draft && <small>草稿</small>}
                  <span className="history-name">{c.title}</span>
                </button>
                {!c.draft && (
                  <Dropdown
                    trigger={['click']}
                    menu={{
                      items: [
                        { key: 'rename', label: '重命名' },
                        { key: 'archive', label: archived ? '恢复会话' : '归档' },
                      ],
                      onClick: ({ key }) => (key === 'rename' ? setRename(c) : void editChat(c.id, { archived: !archived })),
                    }}
                  >
                    <button className="history-more" aria-label={'管理会话 ' + c.title}>
                      <Icon name="more" />
                    </button>
                  </Dropdown>
                )}
              </div>
            ))}
          </nav>
        </div>
        <div className="sidebar-footer">
          <button className="account-button" onClick={() => go('/profile')}>
            <UserAvatar name={user.display_name || user.username} />
            <span className="account-label">
              <strong>{user.display_name || user.username}</strong>
              <small>{user.department?.name}</small>
            </span>
          </button>
          <Button type="text" title="退出登录" aria-label="退出登录" onClick={logout}>
            <Icon name="logout" />
          </Button>
        </div>
      </aside>
      <button className="mobile-backdrop" aria-label="关闭菜单" onClick={() => setMenu(false)} />
      <main id="workspace" className="workspace" tabIndex={-1}>
        <header className="topbar">
          <button className="icon-button menu-button" aria-label="显示菜单" onClick={() => setMenu(true)}>
            <Icon name="menu" />
          </button>
          <div className="topbar-title">
            <Icon name={links.find((l) => l.selected)?.icon || 'messages'} />
            <strong>{links.find((l) => l.selected)?.name}</strong>
            {current && chatting && <span>{current.title}</span>}
          </div>
        </header>
        {warning && (
          <p role="alert" className="storage-warning">
            {warning}
          </p>
        )}
        {personal ? (
          <div className="page data-page">
            <h1>个人资料</h1>
            <Profile user={user} token={token} update={update} logout={logout} />
          </div>
        ) : usage ? (
          <div className="page data-page">
            <Usage token={token} />
          </div>
        ) : admin ? (
          <div className="page data-page">
            <h1>{admin.params.section === 'usage' ? '企业用量' : '管理台'}</h1>
            {user.system_admin ? (
              <Administration token={token} section={admin.params.section!} choose={(v) => go('/admin/' + v)} />
            ) : (
              <p>当前账号无权访问管理台。</p>
            )}
          </div>
        ) : !kb ? (
          <div className="page">当前账号尚未加入知识库。</div>
        ) : docs ? (
          <Documents
            canWrite={user.system_admin}
            key={kb.id}
            kb={kb}
            token={token}
            libraries={libraries}
            choose={(v) => go('/kb/' + v + '/documents')}
          />
        ) : (
          <div className="page chat-view">
            <ChatView
              key={id}
              token={token}
              mode={d.mode}
              setMode={(mode) => {changeDraft({ mode }); if(mode==='knowledge') window.dispatchEvent(new Event('nabucite:libraries'))}}
              userName={user.display_name || user.username}
              active={isActive(id)}
              onStop={()=>{
                const answer=messages[id]?.at(-1)
                const q=messages[id]?.find(m=>m.role==='user' && (m.id===answer?.reply_to || m.request_id===answer?.request_id))
                void stop(id,answer?.request_id && q ? {request_id:answer.request_id,question:q.content,mode:q.mode??d.mode}:undefined)
              }}
              onRegenerate={(qid)=>{
                const q=messages[id]?.find(m=>m.id===qid && m.role==='user')
                if(q) void send(id,{request_id:crypto.randomUUID(),question:q.content,mode:q.mode??d.mode,regenerate:qid},false,()=>{})
              }}
              draft={d.text}
              setDraft={(text) => changeDraft({ text })}
              messages={chatId && verified === chatId ? (messages[id] ?? []) : []}
              disabled={archived || Boolean(chatId && (!current || verified !== id))}
              libraries={libraries}
              kbId={kb.id}
              selectLibrary={(v) => go('/kb/' + v + '/draft/' + crypto.randomUUID())}
              onSend={submit}
            />
          </div>
        )}
      </main>
      <Modal
        title="重命名会话"
        open={Boolean(rename)}
        onCancel={() => setRename(undefined)}
        onOk={() => rename && editChat(rename.id, { title: rename.title })}
        okButtonProps={{ disabled: !rename?.title.trim() }}
      >
        <Input
          aria-label="会话标题"
          maxLength={100}
          value={rename?.title}
          onChange={(e) => rename && setRename({ ...rename, title: e.target.value })}
        />
      </Modal>
    </div>
  )
}
