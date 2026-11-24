import { Button, Form, Input } from 'antd'
import { useState } from 'react'
import { json, reason } from './api'

export function Login({ onLogin, notice }: { onLogin: (token: string) => void; notice: string }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(values: { username: string; password: string }) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const session = await json<{ access_token: string }>('/auth/login', null, { method: 'POST', body: JSON.stringify(values) })
      onLogin(session.access_token)
    } catch (err) { setError(reason(err)) }
    finally { setBusy(false) }
  }
  return <main className="login-page"><section className="panel login-panel">
    <strong className="login-brand">NabuCite</strong><h1>登录企业知识库</h1>
    <Form layout="vertical" onFinish={submit} requiredMark={false}>
      <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}><Input autoComplete="username" autoFocus /></Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password autoComplete="current-password" /></Form.Item>
      {(error || notice) && <p className="login-error" role="alert">{error || notice}</p>}
      <Button block type="primary" htmlType="submit" loading={busy}>登录</Button>
    </Form>
  </section></main>
}
