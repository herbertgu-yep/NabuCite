import { useEffect, useState } from 'react'
import { Button, Modal } from 'antd'
import { json, reason, request } from './api'
import type { Citation } from './conversations'

type Passage = Pick<Citation, 'filename' | 'section' | 'page_start' | 'page_end'> & { text: string }

export function Source({
  token,
  kb,
  document,
  filename,
  citation,
  close,
}: {
  token: string
  kb: string
  document: string
  filename: string
  citation?: Citation
  close: () => void
}) {
  const [snippet, setSnippet] = useState<Passage>()
  const [showFile, setShowFile] = useState(!citation)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const pdf = /\.pdf$/i.test(filename)
  const unit = pdf ? '页' : /\.(md|markdown)$/i.test(filename) ? '行' : '段'
  const path = `/knowledge-bases/${kb}/documents/${document}`
  useEffect(() => {
    const stop = new AbortController()
    let objectUrl = ''
    async function load() {
      if (citation) {
        const data = await json<Passage>(path + '/chunks/' + citation.chunk_id, token, { signal: stop.signal, cache: 'no-store' })
        if (!stop.signal.aborted) setSnippet(data)
      }
      if (showFile) {
        const file = await (await request(path + '/original', token, { signal: stop.signal, cache: 'no-store' })).blob()
        if (!stop.signal.aborted) {
          objectUrl = URL.createObjectURL(file)
          setUrl(pdf ? objectUrl + '#page=' + (citation?.page_start ?? 1) : objectUrl)
        }
      }
    }
    void load().catch((e) => {
      if (!stop.signal.aborted) {
        setSnippet(undefined)
        setUrl('')
        setError(reason(e))
      }
    })
    return () => {
      stop.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [token, path, citation, showFile, pdf])
  useEffect(() => {
    function hidden() {
      if (window.document.hidden) close()
    }
    window.document.addEventListener('visibilitychange', hidden)
    return () => window.document.removeEventListener('visibilitychange', hidden)
  }, [close])
  return (
    <Modal open title={filename} width={840} onCancel={close} footer={null}>
      {error && <p role="alert">{error}</p>}
      {snippet && (
        <>
          <p>
            第 {snippet.page_start}
            {snippet.page_end !== snippet.page_start && '–' + snippet.page_end} {unit} · {snippet.section}
          </p>
          <blockquote>{snippet.text}</blockquote>
        </>
      )}
      {!showFile && snippet && <Button onClick={() => setShowFile(true)}>{pdf ? '打开 PDF 对应页' : '获取原文件'}</Button>}
      {showFile && (url ? (pdf ? <iframe className="pdf-preview" src={url} title="PDF 原文" /> : <a href={url} download={filename}>下载 {filename}</a>) : !error && <p>正在读取原件…</p>)}
    </Modal>
  )
}
