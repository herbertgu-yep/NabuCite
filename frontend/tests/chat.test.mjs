import assert from 'node:assert/strict'
import test from 'node:test'
import { readChatStream } from '../src/api.ts'
import { updateAnswer } from '../src/conversations.ts'

const chatId = crypto.randomUUID()
const event = (name, fields = {}) =>
  `event: ${name}\r\ndata: ${JSON.stringify({ conversation_id: chatId, ...fields })}\r\n\r\n`

function bytes(wire, width) {
  const data = new TextEncoder().encode(wire)
  return new ReadableStream({
    start(controller) {
      for (let i = 0; i < data.length; i += width) controller.enqueue(data.slice(i, i + width))
      controller.close()
    },
  })
}

for (const width of [1, 2, 17, 4096]) {
  test(`SSE protocol survives ${width}-byte reads`, async () => {
    const parts = []
    const wire = ': keep-alive\r\n\r\n' + event('delta', { text: '蓝' }) + event('delta', { text: '杉\n' }) + event('done')
    await readChatStream(bytes(wire, width), chatId, text => parts.push(text))
    assert.deepEqual(parts, ['蓝', '杉\n'])
  })
}

test('incomplete stream preserves delivered bytes and signals interruption', async () => {
  const parts = []
  await assert.rejects(readChatStream(bytes(event('delta', { text: '一' }) + 'event: do', 9), chatId,
    text => parts.push(text)), /提前关闭/)
  assert.deepEqual(parts, ['一'])
})

test('upstream error and conversation mismatch cannot complete a reply', async () => {
  await assert.rejects(readChatStream(bytes(event('error', { message: '模型凭据无效' }), 5), chatId, assert.fail), /凭据无效/)
  await assert.rejects(readChatStream(bytes(event('delta', { conversation_id: 'other', text: 'x' }), 7), chatId, assert.fail), /不匹配/)
})

test('answer updates target one message ID and completed answers stay final', () => {
  const user = { role: 'user', id: crypto.randomUUID(), content: '请记住代号蓝杉' }
  const answer = { role: 'assistant', id: crypto.randomUUID(), content: '', status: 'streaming' }
  const messages = [user, answer]
  assert.deepEqual(updateAnswer(messages, user.id, { type: 'delta', text: 'x' }), messages)
  const partial = updateAnswer(messages, answer.id, { type: 'delta', text: '一' })
  const done = updateAnswer(partial, answer.id, { type: 'done' })
  assert.equal(done[1].content, '一')
  assert.deepEqual(updateAnswer(done, answer.id, { type: 'error', text: 'late' }), done)
})

test('start associates the committed server message IDs before the first delta', async () => {
  const seen = []
  const ids = { user_message_id: crypto.randomUUID(), answer_id: crypto.randomUUID() }
  await readChatStream(bytes(event('start', ids) + event('delta', {text: '文', answer_id: ids.answer_id}) + event('done', {answer_id: ids.answer_id}), 1),
    chatId, text => seen.push(text), (...pair) => seen.push(pair))
  assert.deepEqual(seen, [[ids.user_message_id, ids.answer_id], '文'])
})

test('done delivers only the committed citations for the started answer', async () => {
  const ids = { user_message_id: crypto.randomUUID(), answer_id: crypto.randomUUID() }
  const citation = { number: 1, document_id: crypto.randomUUID(), chunk_id: crypto.randomUUID(),
    knowledge_base_id: crypto.randomUUID(), filename: '原件.pdf', page_start: 3, page_end: 3, section:'规程' }
  let result
  const finish = body => readChatStream(bytes(event('start', ids) + event('done', { answer_id:ids.answer_id, ...body }), 1),
    chatId, assert.fail, ()=>{}, value=>{result=value})
  await finish({citations:[citation]})
  assert.deepEqual(result, [citation])
  await assert.rejects(finish({citations:[{number:1}]}), /引用格式/)
})

test('progress validates both ids and preserves the latest observed stage on disconnect', async () => {
  const start = event('start',{answer_id:'a',user_message_id:'u'})
  const step={stage:'retrieve',status:'active',seq:1}
  const progress=event('progress',{answer_id:'a',...step})
  const messages=[{role:'assistant',id:'b',status:'streaming',content:''},{role:'assistant',id:'a',status:'streaming',content:''}]
  let state=messages
  const apply=s=>{state=updateAnswer(state,'a',{type:'progress',step:s})}
  await assert.rejects(readChatStream(bytes(start+progress+progress,2),chatId,assert.fail,()=>{},undefined,apply),/提前关闭/)
  assert.deepEqual(state[1].progress,[step]);assert.equal(state[0],messages[0])
  for (const wrong of [{answer_id:'b'},{conversation_id:'other'}]) {
    await assert.rejects(readChatStream(bytes(start+event('progress',{answer_id:'a',...step,...wrong}),20),chatId,assert.fail,()=>{},undefined,apply),/不匹配/)
  }
})

test('two searches retain separate durations and a withdrawn answer ignores late frames', () => {
  let messages = [{id:'a',role:'assistant',status:'streaming',content:''}]
  for (const [seq, id, status] of [[1,'retrieve-1','active'],[2,'retrieve-1','complete'],[3,'retrieve-3','active']]) {
    messages = updateAnswer(messages,'a',{type:'progress',step:{id,seq,stage:'retrieve',status,elapsed_ms:seq}})
  }
  assert.deepEqual(messages[0].progress.map(s=>s.id),['retrieve-1','retrieve-3'])
  messages = [{...messages[0],status:'withdrawn',content:'',citations:[],progress:[]}]
  assert.deepEqual(updateAnswer(messages,'a',{type:'delta',text:'迟到正文'}),messages)
  assert.deepEqual(updateAnswer(messages,'a',{type:'done',citations:[]}),messages)
})
