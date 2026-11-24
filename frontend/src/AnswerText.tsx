import type { ReactNode } from 'react'
import type { Citation } from './conversations'

/** 只渲染聊天支持的 Markdown 子集。所有正文都作为 React 文本节点。 */
export function AnswerText({ text, citations, open }: { text: string; citations: Citation[]; open: (c: Citation) => void }) {
  function inline(line: string): ReactNode[] {
    return line.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, index) => {
      const citation = /^\[(\d+)\]$/.exec(part)
      const source = citation && citations.find((c) => c.number === Number(citation[1]))
      if (source)
        return (
          <button className="source-link" key={index} onClick={() => open(source)} aria-label={`查看引用 ${source.number}`}>
            {part}
          </button>
        )
      return part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part
    })
  }
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\||\|$/g, '')
      .split('|')
      .map((s) => s.trim())
  for (let i = 0; i < lines.length; ) {
    const start = i
    const line = lines[i++]
    if (!line.trim()) continue
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i] ?? '')) {
      i++
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|')) rows.push(cells(lines[i++]))
      blocks.push(
        <div className="table-scroll" key={start}>
          <table>
            <thead>
              <tr>
                {cells(line).map((s, k) => (
                  <th key={k}>{inline(s)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, k) => (
                <tr key={k}>
                  {row.map((s, n) => (
                    <td key={n}>{inline(s)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    } else if (/^\s*(?:[-*]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line)
      const pattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/
      const items = [line.replace(pattern, '')]
      while (i < lines.length && pattern.test(lines[i])) items.push(lines[i++].replace(pattern, ''))
      const List = ordered ? 'ol' : 'ul'
      blocks.push(
        <List key={start}>
          {items.map((s, k) => (
            <li key={k}>{inline(s)}</li>
          ))}
        </List>
      )
    } else if (/^#{1,6} /.test(line)) blocks.push(<h4 key={start}>{inline(line.replace(/^#+ /, ''))}</h4>)
    else blocks.push(<p key={start}>{inline(line)}</p>)
  }
  return <div className="answer-body">{blocks}</div>
}
