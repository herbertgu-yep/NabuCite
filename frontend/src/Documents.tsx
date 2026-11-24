import { useEffect, useRef, useState } from 'react'
import { Button, Empty, Input, Select } from 'antd'
import { Icon } from './Icon'
import { Source } from './Source'
import { json, reason } from './api'
import type { Library } from './conversations'

const labels = { pending: '待处理', processing: '处理中', ready: '可用', failed: '失败' }
interface Doc {
  id: string
  filename: string
  status: keyof typeof labels
  failure?: string
  chunk_count: number
  updated_at: string
}

export function Documents({
  kb,
  canWrite,
  manage = false,
  token,
  libraries,
  choose,
}: {
  kb: Library
  manage?: boolean
  canWrite: boolean
  token: string
  libraries: Library[]
  choose: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [rows, setRows] = useState<Doc[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [opened, setOpened] = useState<Doc>()
  const input = useRef<HTMLInputElement>(null)
  const path = `/knowledge-bases/${kb.id}/documents`
  useEffect(() => {
    const stop = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function read() {
      try {
        const data = await json<Doc[]>(path + (manage ? '?manage=true' : ''), token, { signal: stop.signal })
        if (stop.signal.aborted) return
        setRows(data)
        setError('')
        setLoading(false)
        if (data.some((d) => ['pending', 'processing'].includes(d.status))) timer = setTimeout(read, 1500)
      } catch (e) {
        if (!stop.signal.aborted) {
          setLoading(false)
          setRows([])
          setOpened(undefined)
          setError(reason(e))
        }
      }
    }
    void read()
    window.addEventListener('focus', read)
    return () => {
      stop.abort()
      clearTimeout(timer)
      window.removeEventListener('focus', read)
    }
  }, [path, token, revision, manage])
  async function submit(file?: File, doc?: Doc) {
    if (!file && !doc) return
    setBusy(true)
    setError('')
    try {
      if (file)
        await json(path, token, {
          method: 'POST',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) },
        })
      else await json(path + '/' + doc!.id + '/retry', token, { method: 'POST' })
      setRevision((n) => n + 1)
    } catch (e) {
      setError(reason(e))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }
  const visible = rows.filter(
    (d) => (!status || status === d.status) && d.filename.toLowerCase().includes(query.trim().toLowerCase()),
  )
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>文档管理</h1>
          <p>
            {kb.sources
              .map(
                (s) =>
                  `${s.kind === 'department' ? '部门 · ' : s.kind === 'project' ? '项目组 · ' : ''}${s.name}（${
                    s.role === 'admin' ? '管理员' : '读者'
                  }）`,
              )
              .join('、')}
          </p>
        </div>
        <input type="file" hidden ref={input} accept=".pdf,.doc,.docx,.md,.markdown" onChange={(e) => void submit(e.target.files?.[0])} />
        {canWrite && (
          <Button type="primary" disabled={busy} onClick={() => input.current?.click()} icon={<Icon name="upload" />}>
            上传文档
          </Button>
        )}
      </header>
      {error && <p role="alert">{error}</p>}
      <header className="doc-filters filters">
        <Select
          aria-label="文档所属知识库"
          value={kb.id}
          options={libraries.map((l) => ({ value: l.id, label: l.name }))}
          onChange={choose}
        />

        <Select
          aria-label="索引状态"
          value={status}
          onChange={setStatus}
          options={[{ value: '', label: '全部状态' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))]}
        />
        <Input placeholder="搜索文档" value={query} onChange={(e) => setQuery(e.target.value)} />
      </header>
      <section className="panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {['文件名', '入库状态', '片段数', '更新日期', '操作'].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((d) => (
                <tr key={d.id} data-document-id={d.id}>
                  <td>{d.filename}</td>
                  <td>
                    <span className={'document-status ' + d.status}>{labels[d.status]}</span> {d.failure}
                  </td>
                  <td>{d.chunk_count}</td>
                  <td>{new Date(d.updated_at).toLocaleString('zh-CN', { hour12: false })}</td>
                  <td>
                    <Button onClick={() => setOpened(d)}>预览原文</Button>
                    {canWrite &&
                      (d.status === 'failed' || (d.status === 'pending' && Date.now() - Date.parse(d.updated_at) > 300000)) && (
                        <Button disabled={busy} onClick={() => void submit(undefined, d)}>
                          重试
                        </Button>
                      )}
                  </td>
                </tr>
              ))}
              {!visible.length && (
                <tr>
                  <td colSpan={5}>
                    <Empty
                      description={
                        loading ? '正在读取文档…' : error ? '列表暂不可用' : rows.length ? '没有符合条件的文档' : '暂无文档'
                      }
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      {opened && (
        <Source token={token} kb={kb.id} document={opened.id} filename={opened.filename} close={() => setOpened(undefined)} />
      )}
    </div>
  )
}
