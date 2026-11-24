import { useEffect, useRef, useState } from 'react'
import { Button, Form, Input, InputNumber, Select, Switch, Tabs, Table, Drawer, Modal, Popconfirm, Segmented } from 'antd'
import { json, reason } from './api'
import type { Org, User } from './conversations'
import { Documents } from './Documents'
import { Usage } from './Usage'

interface Directory {
  users: User[]
  organizations: Org[]
  libraries: { id: string; name: string; deleted: boolean; documents: number }[]
  grants: { kind: string; subject: string; kb: string; role: string }[]
}
interface Version {
  id: string
  model: string
  base_url: string
  credential: string
  window: number
  output: number
  published: boolean
  active: boolean
}
const required = [{ required: true, message: '请填写此项' }]
const selector = (items: { id: string; name: string }[]) => items.map((o) => ({ value: o.id, label: o.name }))

type Limit = { value: number; source: string; url?: string }
type Specs = { limits: Partial<Record<'window' | 'output', Limit>>; note: string }
type Target = Pick<Version, 'model' | 'base_url' | 'credential'>

function ModelDraft({ active, token, busy, save }: {
  active?: Version; token: string; busy: boolean; save: (v: unknown) => void
}) {
  const [form] = Form.useForm()
  const [target, setTarget] = useState<Target>(() => ({
    model: active?.model ?? '', base_url: active?.base_url ?? '', credential: active?.credential ?? 'MODEL_API_KEY',
  }))
  const [specs, setSpecs] = useState<Specs>()
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const edited = useRef(new Set<string>())
  const generation = useRef(0)

  useEffect(() => {
    const stop = new AbortController()
    const current = generation.current
    if (!target.base_url || !target.model || !target.credential) {
      setLoading(false)
      setStatus('')
      return
    }
    setLoading(true)
    setStatus('正在读取服务商模型规格…')
    const timer = setTimeout(async () => {
      try {
        const result = await json<Specs>('/admin/models/limits', token, {
          method: 'POST', body: JSON.stringify(target), signal: stop.signal,
        })
        if (stop.signal.aborted || generation.current !== current) return
        for (const field of ['window', 'output'] as const) {
          if (!edited.current.has(field)) form.setFieldValue(field, result.limits[field]?.value)
        }
        setSpecs(result)
        setStatus(result.note)
      } catch (error) {
        if (!stop.signal.aborted && generation.current === current) setStatus(reason(error))
      } finally {
        if (!stop.signal.aborted && generation.current === current) setLoading(false)
      }
    }, 400)
    return () => { clearTimeout(timer); stop.abort() }
  }, [form, target, token])

  return (
    <Form<Target & { window?: number; output?: number }> form={form} layout="vertical" initialValues={target} onFinish={save}
      onValuesChange={(changed, values) => {
        if (['base_url','model','credential'].some(k => k in changed)) {
          generation.current++
          edited.current.clear()
          form.setFieldsValue({ window: undefined, output: undefined })
          setSpecs(undefined)
          setTarget({ base_url: values.base_url, model: values.model, credential: values.credential })
        } else Object.keys(changed).forEach(k => edited.current.add(k))
      }}
    >
      <Form.Item name="base_url" label="HTTPS 服务地址" rules={required}><Input /></Form.Item>
      <Form.Item name="model" label="模型标识" rules={required}><Input /></Form.Item>
      <Form.Item name="credential" label="后端凭据变量名" rules={required}><Input /></Form.Item>
      {(['window','output'] as const).map(field => {
        const limit = specs?.limits[field]
        const source = limit ? limit.source + ' · ' + limit.value.toLocaleString() + ' Token' : ''
        return <Form.Item key={field} name={field} label={field === 'window' ? '上下文窗口' : '输出上限'} rules={required}
          extra={limit?.url ? <a href={limit.url} target="_blank" rel="noreferrer">{source}</a> : source || (!loading && '未取得服务商规格，请手动填写')}>
          <InputNumber min={field === 'window' ? 1024 : 32} precision={0} style={{width:180}} />
        </Form.Item>
      })}
      {status && <p role="status">{status}</p>}
      <Button disabled={loading} loading={busy} htmlType="submit">保存为新草稿</Button>
    </Form>
  )
}

export function Administration({ token, section, choose }: { token: string; section: string; choose: (v: string) => void }) {
  const [search, setSearch] = useState('')
  const [department, setDepartment] = useState('')
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [action, setAction] = useState('join')
  const [org, setOrg] = useState<string>()
  const [drawer, setDrawer] = useState(false)
  const [managed, setManaged] = useState<string>()
  const [peopleView,setPeopleView]=useState('成员')
  const [createOrg,setCreateOrg]=useState(false)
  const [access,setAccess]=useState<string>()
  const [adding,setAdding]=useState(false)
  const [libSearch, setLibSearch] = useState('')
  const [editing, setEditing] = useState<{ id?: string; name: string }>()
  const [directory, setDirectory] = useState<Directory>()
  const [versions, setVersions] = useState<Version[]>([])
  const [revision, refresh] = useState(0)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [checks, setChecks] = useState<Record<string, { state: 'pending' | 'success' | 'error'; text: string }>>({})
  async function verify(id: string) {
    setChecks(prev => ({ ...prev, [id]: { state: 'pending', text: '正在等待模型响应…' } }))
    try {
      await json(`/admin/models/${id}/verify`, token, { method: 'POST' })
      setChecks(prev => ({ ...prev, [id]: { state: 'success', text: '连接成功，已收到模型回复。' } }))
    } catch (error) {
      setChecks(prev => ({ ...prev, [id]: { state: 'error', text: '验证失败：' + reason(error) } }))
    }
  }
  const [employee, setEmployee] = useState('')
  const [kind, setKind] = useState('user')
  const [employeeForm] = Form.useForm()
  const [grantForm] = Form.useForm()
  useEffect(() => {
    const stop = new AbortController()
    void Promise.all([
      json<Directory>('/admin/directory', token, { signal: stop.signal }),
      json<Version[]>('/admin/models', token, { signal: stop.signal }),
    ])
      .then(([d, v]) => {
        if (!stop.signal.aborted) {
          setDirectory(d)
          setVersions(v)
        }
      })
      .catch((e) => {
        if (!stop.signal.aborted) setNotice(reason(e))
      })
    return () => stop.abort()
  }, [token, revision])
  async function save(path: string, method: string, body?: unknown) {
    setBusy(true)
    setNotice('')
    try {
      const result = await json<{ success?: string[]; failed?: Record<string, string> }>('/admin/' + path, token, {
        method,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      setNotice(
        result?.success
          ? '成功 ' +
              result.success.length +
              ' 人。' +
              Object.entries(result.failed ?? {})
                .map(([id, r]) => (directory?.users.find((u) => u.id === id)?.username ?? id) + '：' + r)
                .join('；')
          : '操作成功',
      )
      setSelected([])
      setEditing(undefined)
      setDrawer(false)
      setCreateOrg(false)
      setAdding(false)
      window.dispatchEvent(new Event('nabucite:libraries'))
      refresh((n) => n + 1)
    } catch (e) {
      setNotice(reason(e))
    } finally {
      setBusy(false)
    }
  }
  if (!directory) return <p role="status">{notice || '正在读取管理配置…'}</p>
  const users = directory.users.map((u) => ({ id: u.id, name: u.username }))
  const departments = selector(directory.organizations.filter((o) => o.kind === 'department'))
  const projects = selector(directory.organizations.filter((o) => o.kind === 'project'))
  const active = versions.find((v) => v.active)
  const name = (id: string) => [...users, ...directory.organizations, ...directory.libraries].find((i) => i.id === id)?.name
  return (
    <>
      {notice && <p role="status">{notice}</p>}
      <Tabs
        activeKey={section}
        onChange={choose}
        items={[
          {
            key: 'people',
            label: '成员与组织',
            children: (
              <>
                <div className="settings-toolbar"><Segmented value={peopleView} options={['成员','组织']} onChange={setPeopleView}/>
                {peopleView==='组织' && <Button type="primary" onClick={()=>setCreateOrg(true)}>新建组织</Button>}</div>
                {peopleView==='成员' ? <>
                <div className="control-row">
                  {selected.length ? (
                    <>
                      <strong>已选 {selected.length} 人</strong>
                      <Button onClick={() => setSelected([])}>清空</Button>
                      <Select
                        value={action}
                        onChange={(v) => {
                          setAction(v)
                          setOrg(undefined)
                        }}
                        options={Object.entries({
                          join: '加入项目组',
                          leave: '移出项目组',
                          department: '迁移主部门',
                          enable: '启用',
                          disable: '停用',
                        }).map(([value, label]) => ({ value, label }))}
                      />
                      {!['enable', 'disable'].includes(action) && (
                        <Select
                          placeholder="选择组织"
                          value={org}
                          onChange={setOrg}
                          options={action === 'department' ? departments : projects}
                        />
                      )}
                      <Popconfirm
                        title={'确认批量操作 ' + selected.length + ' 位员工？'}
                        onConfirm={() => save('employees/batch', 'POST', { action, ids: selected, org_id: org })}
                      >
                        <Button disabled={busy || (!org && !['enable', 'disable'].includes(action))}>执行</Button>
                      </Popconfirm>
                    </>
                  ) : (
                    <>
                      <Input
                        style={{ width: 180 }}
                        placeholder="搜索姓名或用户名"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                      <Select
                        value={department}
                        onChange={setDepartment}
                        options={[{ value: '', label: '全部部门' }, ...departments]}
                      />
                      <Select
                        value={status}
                        onChange={setStatus}
                        options={[
                          { value: '', label: '所有状态' },
                          { value: 'yes', label: '启用' },
                          { value: 'no', label: '停用' },
                        ]}
                      />
                      <Button
                        type="primary"
                        onClick={() => {
                          setEmployee('')
                          employeeForm.resetFields()
                          setDrawer(true)
                        }}
                      >
                        新增员工
                      </Button>
                    </>
                  )}
                </div>
                <Table
                  rowKey="id"
                  scroll={{ x: 700 }}
                  pagination={{ pageSize: 10, onChange: () => setSelected([]) }}
                  rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
                  dataSource={directory.users.filter(
                    (u) =>
                      (u.display_name + u.username).includes(search) &&
                      (!department || department === u.department?.id) &&
                      (!status || u.enabled === (status === 'yes')),
                  )}
                  columns={[
                    {
                      title: '姓名',
                      render: (_, u) => (
                        <Button
                          type="link"
                          onClick={() => {
                            setEmployee(u.id)
                            employeeForm.setFieldsValue({
                              ...u,
                              password: '',
                              department_id: u.department?.id,
                              projects: u.projects.map((p) => p.id),
                            })
                            setDrawer(true)
                          }}
                        >
                          {u.display_name || u.username}
                        </Button>
                      ),
                    },
                    { title: '用户名', dataIndex: 'username' },
                    { title: '主部门', render: (_, u) => u.department?.name },
                    { title: '项目组', render: (_, u) => u.projects.map((p) => p.name).join('、') || '—' },
                    { title: '状态', render: (_, u) => (u.enabled ? '启用' : '停用') },
                  ]}
                />
                <Drawer forceRender open={drawer} title={employee ? '成员详情' : '新增员工'} onClose={() => setDrawer(false)}>
                  <Form
                    form={employeeForm}
                    layout="vertical"
                    initialValues={{ enabled: true, projects: [] }}
                    onFinish={(v) =>
                      void save('employees/' + (employee || crypto.randomUUID()), 'PUT', { ...v, password: v.password || null })
                    }
                  >
                    <Form.Item name="username" label="登录用户名" rules={required}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="display_name" label="显示名" rules={required}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="department_id" label="主部门" rules={required}>
                      <Select options={departments} />
                    </Form.Item>
                    <Form.Item name="projects" label="项目组">
                      <Select mode="multiple" options={projects} />
                    </Form.Item>
                    <Form.Item
                      name="password"
                      label={employee ? '重置密码（可留空）' : '初始密码（至少 15 字符）'}
                      rules={employee ? [] : required}
                    >
                      <Input.Password autoComplete="new-password" />
                    </Form.Item>
                    <Form.Item name="enabled" label="启用账号" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                    <Button loading={busy} htmlType="submit">
                      保存员工
                    </Button>
                  </Form>
                </Drawer>
                </> : (
                  <Table
                    rowKey="id"
                    dataSource={directory.organizations.map((item) => ({
                      ...item,
                      members: directory.users.filter((u) => u.department?.id === item.id || u.projects.some((p) => p.id === item.id)),
                    }))}
                    columns={[
                      { title: '组织名称', dataIndex: 'name' },
                      { title: '类型', render: (_, item) => item.kind === 'department' ? '部门' : '项目组' },
                      { title: '成员数', render: (_, item) => item.members.length },
                    ]}
                    expandable={{
                      expandedRowRender: (item) => item.members.map((u) => u.display_name + ' (@' + u.username + ')').join('、') || '暂无成员',
                    }}
                  />
                )}
                <Drawer title="新建组织" open={createOrg} destroyOnHidden onClose={()=>setCreateOrg(false)}>

                <Form
                  layout="vertical"
                  onFinish={(v) => void save('organizations', 'POST', v)}
                  initialValues={{ kind: 'department' }}
                >
                  <Form.Item name="kind" label="组织类型">
                    <Select
                      options={[
                        { value: 'department', label: '部门' },
                        { value: 'project', label: '项目组' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="name" label="组织名称" rules={required}>
                    <Input placeholder="组织名称" />
                  </Form.Item>
                  <Button loading={busy} htmlType="submit">
                    创建组织
                  </Button>
                </Form></Drawer>
              </>
            ),
          },
          {
            key: 'access',
            label: '知识库管理',
            children: (
              <>
                <div className="control-row">
                  <Input
                    placeholder="搜索知识库"
                    style={{ width: 220 }}
                    value={libSearch}
                    onChange={(e) => setLibSearch(e.target.value)}
                  />
                  <Button type="primary" onClick={() => setEditing({ name: '' })}>
                    新建知识库
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  scroll={{ x: 550 }}
                  dataSource={directory.libraries.filter((k) => k.name.includes(libSearch))}
                  columns={[
                    { title: '知识库', dataIndex: 'name' },
                    { title: '文档数', dataIndex: 'documents' },
                    { title: '状态', render: (_, k) => (k.deleted ? '已删除' : '开放') },
                    {
                      title: '操作',
                      render: (_, k) => (
                        <>
                          <Button type="link" onClick={() => {setAccess(k.id);setAdding(false)}}>访问权限</Button>
                          <Button type="link" onClick={() => setEditing(k)}>
                            改名
                          </Button>
                          {!k.deleted && (
                            <Button type="link" onClick={() => setManaged(k.id)}>
                              管理文档
                            </Button>
                          )}{' '}
                          <Popconfirm
                            title={(k.deleted ? '恢复' : '删除') + '「' + k.name + '」？'}
                            description={k.documents + ' 份文档的访问权限将随知识库状态改变。'}
                            onConfirm={() => save('libraries/' + k.id, 'PATCH', { deleted: !k.deleted })}
                          >
                            <Button type="text" danger={!k.deleted}>
                              {k.deleted ? '恢复' : '删除'}
                            </Button>
                          </Popconfirm>
                        </>
                      ),
                    },
                  ]}
                />
                <Drawer width="min(920px,100vw)" open={!!managed} title="库内文档" onClose={() => setManaged(undefined)}>
                  {managed &&
                    directory.libraries
                      .filter((k) => k.id === managed && !k.deleted)
                      .map((k) => (
                        <Documents
                          key={k.id}
                          kb={{ ...k, role: 'admin', sources: [] }}
                          libraries={directory.libraries
                            .filter((l) => !l.deleted)
                            .map((l) => ({ ...l, role: 'admin', sources: [] }))}
                          canWrite
                          manage
                          choose={setManaged}
                          token={token}
                        />
                      ))}
                </Drawer>
                <Modal
                  title={editing?.id ? '修改库名' : '新建知识库'}
                  open={!!editing}
                  onCancel={() => setEditing(undefined)}
                  okButtonProps={{ disabled: !editing?.name.trim() }}
                  onOk={() =>
                    editing &&
                    save('libraries' + (editing.id ? '/' + editing.id : ''), editing.id ? 'PATCH' : 'POST', { name: editing.name })
                  }
                >
                  <Input maxLength={160} value={editing?.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                </Modal>
                <Drawer
                  title={(name(access || '') ?? '') + ' · 访问权限'}
                  open={!!access}
                  onClose={() => setAccess(undefined)}
                >
                  <Button
                    type="primary"
                    disabled={directory.libraries.find((k) => k.id === access)?.deleted}
                    onClick={() => {setKind('user'); setAdding(true)}}
                  >添加授权</Button>
                  <ul className="grant-list">
                    {directory.grants.filter((g) => g.kb === access).map((g) => {
                      const organization = directory.organizations.find((o) => o.id === g.subject)
                      const source = g.kind === 'user' ? '个人授权' : organization?.kind === 'department' ? '部门授权' : '项目组授权'
                      return (
                        <li key={g.kind + g.subject}>
                          <div>
                            <strong>{name(g.subject)}</strong>
                            <small>{source} · {g.role === 'reader' ? '读者' : '知识库管理员'}</small>
                          </div>
                          <Popconfirm
                            title={'移除「' + name(g.subject) + '」对「' + name(g.kb) + '」的此项授权？'}
                            description="仅通过此来源获得权限的成员将失去访问权。"
                            onConfirm={() => save('grants/' + g.kind + '/' + g.subject + '/' + g.kb, 'PUT', { role: null })}
                          ><Button danger type="link">移除</Button></Popconfirm>
                        </li>
                      )
                    })}
                  </ul>
                  {directory.grants.every((g) => g.kb !== access) && <p>暂无授权</p>}
                  {adding &&
                <Form
                  layout="vertical"
                  clearOnDestroy
                  form={grantForm}
                  initialValues={{ kind: 'user', role: 'reader' }}
                  onFinish={(v) =>
                    void save(`grants/${v.kind}/${v.subject}/${access}`, 'PUT', { role: v.role })
                  }
                >
                  <Form.Item name="kind" label="授权对象类型">
                    <Select
                      onChange={(k) => {
                        setKind(k)
                        grantForm.resetFields(['subject'])
                      }}
                      options={[
                        { value: 'user', label: '个人' },
                        { value: 'organization', label: '部门或项目组' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="subject" label="授权对象" rules={required}>
                    <Select options={selector(kind === 'user' ? users : directory.organizations)} />
                  </Form.Item>
                  <Form.Item name="role" label="角色">
                    <Select
                      options={[
                        { value: 'reader', label: '读者' },
                        { value: 'admin', label: '知识库管理员' },
                      ]}
                    />
                  </Form.Item>
                  <Button loading={busy} htmlType="submit">
                    保存授权
                  </Button>
                </Form>}</Drawer>
              </>
            ),
          },
          {
            key: 'models',
            label: '模型配置',
            children: (
              <>
                <ModelDraft key={active?.id} active={active} token={token} busy={busy} save={v => void save('models', 'POST', v)} />
                {versions.map((v) => (
                  <div className="version-row" key={v.id}>
                    <strong>{v.model}</strong> · {v.window} / {v.output} · {v.active ? '使用中' : v.published ? '历史版本' : '草稿'}
                    <Button loading={checks[v.id]?.state === 'pending'} onClick={() => void verify(v.id)}>
                      验证连接
                    </Button>
                    <span className="model-check" role="status" aria-live="polite" data-error={checks[v.id]?.state === 'error'}>{checks[v.id]?.text}</span>
                    {!v.active && (
                      <Button disabled={busy} onClick={() => void save(`models/${v.id}/publish`, 'POST')}>
                        {v.published ? '回滚到此版本' : '发布'}
                      </Button>
                    )}
                  </div>
                ))}
              </>
            ),
          },
          { key: 'usage', label: '企业用量', children: <Usage embedded token={token} directory={directory} /> },
        ]}
      />
    </>
  )
}
