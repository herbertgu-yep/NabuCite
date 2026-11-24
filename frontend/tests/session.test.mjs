import assert from 'node:assert/strict'
import test from 'node:test'
import { ApiError, request, tokenKey } from '../src/api.ts'

test('an old account response cannot expire the new login', async t => {
  const old = crypto.randomUUID()
  const current = crypto.randomUUID()
  let expired = 0
  globalThis.window = new EventTarget()
  window.addEventListener('nabucite:expired', () => expired++)
  globalThis.sessionStorage = { getItem: key => key === tokenKey ? current : null }
  t.after(() => { delete globalThis.window; delete globalThis.sessionStorage })
  t.mock.method(globalThis, 'fetch', async () => Response.json({}, { status: 401 }))
  await assert.rejects(request('/users/me', old), ApiError)
  assert.equal(expired, 0)
  await assert.rejects(request('/users/me', current), /登录已失效/)
  assert.equal(expired, 1)
})
