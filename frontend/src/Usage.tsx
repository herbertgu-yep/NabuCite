import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Select, Spin, Table } from 'antd'
import { json, reason } from './api'
import type { Org, User } from './conversations'
import { csv, dailyTotals, purposes, sources } from './usageData'
import type { UsageRow } from './usageData'
const UsageTrend = lazy(() => import('./UsageTrend'))

function dates(period: string) {
  const today = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
  const date = new Date(today + 'T00:00:00Z')
  if (period === 'month') date.setUTCDate(1)
  else date.setUTCDate(date.getUTCDate() - (period === '30' ? 29 : 6))
  return [date.toISOString().slice(0, 10), today]
}

export function Usage({ token, directory, embedded=false }: { embedded?:boolean; token: string; directory?: { users: User[]; organizations: Org[] } }) {
  const [period, setPeriod] = useState('7')
  const [[start, end], setDates] = useState(() => dates('7'))
  const [dept, setDept] = useState('')
  const [employee, setEmployee] = useState('')
  const params = new URLSearchParams({ start, end, organization: String(Boolean(directory)) })
  if (dept) params.set('department_id', dept)
  if (employee) params.set('employee_id', employee)
  const query = params.toString()
  const [result, setResult] = useState<{ query: string; rows: UsageRow[]; error?: string }>()
  const [selected, setSelected] = useState('')
  useEffect(() => {
    const stop = new AbortController()
    setSelected('')
    if (!start || !end || end < start) {
      setResult({ query, rows: [], error: '请选择有效的起止日期。' })
      return
    }
    void json<UsageRow[]>('/usage?' + query, token, { signal: stop.signal })
      .then((rows) => {
        if (!stop.signal.aborted) setResult({ query, rows })
      })
      .catch((e) => {
        if (!stop.signal.aborted) setResult({ query, rows: [], error: reason(e) })
      })
    return () => stop.abort()
  }, [query, token, start, end])
  const loaded = result?.query === query
  const rows = useMemo(() => (loaded ? result!.rows : []), [loaded, result])
  const days = useMemo(() => (rows.length ? dailyTotals(start, end, rows) : []), [start, end, rows])
  const error = loaded ? result?.error : ''
  const unknown = rows.reduce((v, r) => v + (r.source === 'unknown' ? r.calls : 0), 0)
  const total = (key: 'input' | 'output' | 'rounds') => rows.reduce((n, r) => n + (r[key] ?? 0), 0)
  const known = rows.some((r) => r.input !== null || r.output !== null)
  function download() {
    if (!loaded || !rows.length || error) return
    const url = URL.createObjectURL(new Blob([csv(rows)], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'NabuCite-' + (directory ? '企业' : '个人') + '用量-' + start + '_' + end + '.csv'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <section className="usage-page">
      {!embedded && <h1>{directory ? '企业用量' : '使用情况'}</h1>}
      <div className="usage-toolbar">
          <Select
            aria-label="统计范围"
            value={period}
            options={[
              ['7', '近 7 天'],
              ['30', '近 30 天'],
              ['month', '本月'],
              ['custom', '自定义'],
            ].map(([value, label]) => ({ value, label }))}
            onChange={(p) => {
              setPeriod(p)
              if (p !== 'custom') setDates(dates(p))
            }}
          />
        {directory && (
          <>
            <Select
              aria-label="筛选部门"
              value={dept}
              onChange={setDept}
              options={[
                { value: '', label: '所有部门' },
                ...directory.organizations.filter((o) => o.kind === 'department').map((o) => ({ value: o.id, label: o.name })),
              ]}
            />
            <Select
              aria-label="筛选员工"
              showSearch={{ optionFilterProp: 'label' }}
              value={employee}
              onChange={setEmployee}
              options={[{ value: '', label: '所有员工' }, ...directory.users.map((u) => ({ value: u.id, label: u.username }))]}
            />
          </>
        )}
          <Button
            disabled={!loaded || !rows.length || !!error}
            title={'导出 ' + start + ' 至 ' + end + ' 的全部明细'}
            onClick={download}
          >
            导出 CSV
          </Button>
      </div>
        {period === 'custom' && (
          <div className="control-row usage-dates">
            <input type="date" aria-label="起始日期" value={start} onChange={(e) => setDates([e.target.value, end])} />
            <input type="date" aria-label="结束日期" value={end} onChange={(e) => setDates([start, e.target.value])} />
          </div>
        )}
      <p className="date-range">
        {start} — {end} · 北京时间
      </p>
      {!loaded ? (
        <Spin description="正在读取用量" />
      ) : error ? (
        <p role="alert">{error}</p>
      ) : !rows.length ? (
        <Empty description="所选日期暂无调用，无可导出的用量" />
      ) : (
        <>
          <div className="metrics">
            {[
              ['总 Token', known ? total('input') + total('output') : '未知'],
              ['输入 Token', known ? total('input') : '未知'],
              ['输出 Token', known ? total('output') : '未知'],
              ['问答轮数', total('rounds')],
            ].map(([name, v]) => (
              <div key={name}>
                <small>{name}</small>
                <strong>
                  {name !== '问答轮数' && rows.some((r) => r.source === 'estimated') ? '≈ ' : ''}
                  {v.toLocaleString()}
                </strong>
              </div>
            ))}
          </div>
          {!!unknown && <p>{unknown} 次调用的 Token 用量未知</p>}
          <section className="data-block">
            <h2>按日趋势</h2>
            <Suspense fallback={<Spin />}>
              <UsageTrend days={days} choose={setSelected} />
            </Suspense>
          </section>
          <section className="data-block">
            <div className="data-heading">
              <h2>用量明细</h2>
              {selected && (
                <Button size="small" onClick={() => setSelected('')}>
                  {selected} ×
                </Button>
              )}
            </div>
            <Table
              size="small"
              rowKey={(r) => r.day + r.purpose + r.source}
              scroll={{ x: 620 }}
              pagination={{ pageSize: 12, hideOnSinglePage: true }}
              dataSource={rows.filter((r) => !selected || r.day === selected)}
              columns={[
                { title: '北京时间日期', dataIndex: 'day' },
                { title: '用途', dataIndex: 'purpose', render: (v: string) => purposes[v] },
                { title: '计量来源', dataIndex: 'source', render: (v: string) => sources[v] },
                { title: '调用', dataIndex: 'calls' },
                { title: '轮数', dataIndex: 'rounds' },
                ...(['input', 'output'] as const).map((k, i) => ({
                  title: i ? '输出 Token' : '输入 Token',
                  dataIndex: k,
                  render: (v: number | null) => v?.toLocaleString() ?? '未知',
                })),
              ]}
            />
          </section>
        </>
      )}
    </section>
  )
}
