import { UserAvatar } from './UserAvatar'
import { useState } from 'react'
import { Button, Form, Input, Tag, Drawer } from 'antd'
import { json, reason } from './api'
import type { User } from './conversations'

export function Profile({
  user,
  token,
  update,
  logout,
}: {
  user: User
  token: string
  update: (u: User) => void
  logout: () => void
}) {
  const [notice, setNotice] = useState('')
  const [edit, setEdit] = useState<'profile' | 'password'>()
  return (
    <section className="profile-form">
      <header className="control-row">
        <UserAvatar name={user.display_name || user.username} />
        <div>
          <h2>{user.display_name || user.username}</h2>@{user.username} · {user.department?.name}
        </div>
      </header>
      <dl className="settings-rows">
        <div><dt>显示名</dt><dd>{user.display_name || user.username}</dd><Button onClick={()=>{setNotice('');setEdit('profile')}}>编辑资料</Button></div>
        <div><dt>登录用户名</dt><dd>@{user.username}</dd></div>
        <div><dt>主部门</dt><dd>{user.department?.name}</dd></div>
        <div><dt>项目组</dt><dd>{user.projects.length ? user.projects.map(p=><Tag key={p.id}>{p.name}</Tag>) : '尚未加入项目组'}</dd></div>
      </dl>
      <h3>账户安全</h3>
      <dl className="settings-rows"><div><dt>登录密码</dt><dd>已设置</dd><Button onClick={()=>{setNotice('');setEdit('password')}}>修改密码</Button></div></dl>
      <Drawer title={edit==='password'?'修改密码':'编辑资料'} open={!!edit} onClose={()=>{setEdit(undefined);setNotice('')}} destroyOnHidden>
        <Form key={edit}
          layout="vertical"
          initialValues={{ display_name: user.display_name || user.username, username: user.username }}
          onFinish={async (v) => {
            try {
              const u = await json<User>('/users/me', token, {
                method: 'PATCH',
                body: JSON.stringify({ ...v, password: v.password || undefined }),
              })
              if (v.password) logout()
              else {
                setEdit(undefined)
                update(u)
                setNotice('个人资料已保存')
              }
            } catch (e) {
              setNotice(reason(e))
            }
          }}
        >
          {edit === 'profile' && (
            <>
              <Form.Item label="显示名" name="display_name">
                <Input />
              </Form.Item>
              <Form.Item extra="修改后请使用新用户名登录" label="登录用户名" name="username">
                <Input />
              </Form.Item>
            </>
          )}
          <Form.Item label="修改用户名或密码时输入当前密码" name="current_password">
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          {edit === 'password' && (
            <Form.Item rules={[{ required: true }]} label="新密码（至少 15 字符）" name="password">
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          )}
          <Button type="primary" htmlType="submit">
            {edit==='password'?'保存密码':'保存个人资料'}
          </Button>
          <Button onClick={() => {setEdit(undefined);setNotice('')}}>取消</Button>
          {notice && <p role="alert">{notice}</p>}
        </Form>
      </Drawer>
      {!edit && <p role="status">{notice}</p>}
    </section>
  )
}
