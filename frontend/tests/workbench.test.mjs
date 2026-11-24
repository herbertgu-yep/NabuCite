import assert from 'node:assert/strict'
import test from 'node:test'
import { acceptDraft, draftTitle, pendingTurn, readDrafts } from '../src/drafts.ts'
import { csv, dailyTotals } from '../src/usageData.ts'

const blank = { id:'local',kb:'kb-a',text:'   ',mode:'knowledge',formal:false }
test('blank drafts have no history item; separate nonempty drafts retain text and mode per user', t=>{
  const first={...blank,text:' 出差报销\n保留原稿 '}
  const next={...blank,id:'another',text:'下次出行',mode:'general'}
  const storage=new Map([['nabucite.drafts.user-a',JSON.stringify({local:first,another:next})]])
  Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{getItem:k=>storage.get(k)}})
  t.after(()=>delete globalThis.sessionStorage)
  assert.equal(draftTitle(blank),'')
  assert.equal(draftTitle(first),'出差报销')
  assert.deepEqual(readDrafts('user-a'),{ local:first, another:next })
  assert.deepEqual(readDrafts('user-b'),{})
  assert.equal(draftTitle({...first,text:''}),'')
})
test('retry retains the request id until receipt; editing during send keeps the next question', ()=>{
  const first={...blank,text:' 出差报销 '}
  const pending=pendingTurn(first)
  const d={...first,pending}
  assert.deepEqual(pendingTurn(JSON.parse(JSON.stringify(d))),pending)
  assert.equal(acceptDraft(d,'other'),d)
  const formal=acceptDraft(d,pending.request_id)
  assert.equal(formal.id,d.id)
  assert.equal(formal.text,'')
  assert.equal(draftTitle({...formal,text:'下一问'}),'')
  assert.equal(acceptDraft({...d,text:'下一问'},pending.request_id).text,'下一问')
})
test('export uses every authorized row, retains BOM, dates, counts and unknown token cells',()=>{
  const rows=[{day:'2026-11-20',purpose:'answer',source:'reported',calls:2,rounds:2,input:17,output:31},
    {day:'2026-11-21',purpose:'rewrite',source:'unknown',calls:1,rounds:0,input:null,output:null}]
  const data=csv(rows)
  assert.equal(data.charCodeAt(0),0xfeff)
  const lines=data.trimEnd().split('\r\n').slice(1).map(l=>l.split(','))
  assert.deepEqual(lines[1],['2026-11-21','检索改写','未知','1','0','',''])
  assert.deepEqual(lines[0].slice(3),['2','2','17','31'])
  const days=dailyTotals('2026-11-19','2026-11-21',rows)
  assert.equal(days.length,3);assert.equal(days[0].calls,0)
  assert.equal(days[1].input,17);assert.equal(days[2].input,null);assert.equal(days[2].unknown,1)
})
