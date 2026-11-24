export interface UsageRow {
  day: string
  purpose: string
  source: string
  input: number | null
  output: number | null
  calls: number
  rounds: number
}
export const purposes: Record<string, string> = {
  embedding: '查询向量化',
  rerank: '重排',
  answer: '回答',
  decision: '工具决策',
  rewrite: '检索改写',
  summary: '历史摘要',
  verify: '连接验证',
}
export const sources: Record<string, string> = { estimated: '估算', reported: '服务商实报', unknown: '未知' }
export function csv(rows: UsageRow[]) {
  const lines = [
    ['北京时间日期', '用途', '计量来源', '调用次数', '问答轮数', '输入 Token', '输出 Token'],
    ...rows.map((r) => [r.day, purposes[r.purpose], sources[r.source], r.calls, r.rounds, r.input, r.output]),
  ]
  return '\uFEFF' + lines.map((line) => line.map((v) => (v == null ? '' : String(v))).join(',')).join('\r\n') + '\r\n'
}
export function dailyTotals(start: string, end: string, rows: UsageRow[]) {
  const values = new Map<string, { day: string; input: number | null; output: number | null; unknown: number; calls: number }>()
  for (let t = Date.parse(start); t <= Date.parse(end); t += 86400000) {
    const day = new Date(t).toISOString().slice(0, 10)
    values.set(day, { day, input: null, output: null, unknown: 0, calls: 0 })
  }
  for (const r of rows) {
    const v = values.get(r.day)
    if (!v) continue
    v.calls += r.calls
    if (r.input !== null) v.input = (v.input ?? 0) + r.input
    if (r.output !== null) v.output = (v.output ?? 0) + r.output
    if (r.source === 'unknown') v.unknown += r.calls
  }
  return Array.from(values.values())
}
