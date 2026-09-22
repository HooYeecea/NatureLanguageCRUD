import { useState, type FormEvent } from 'react'
import { api } from '../api'
import type { Connection, InterpretResult, MutatePreview, QueryResult } from '../types'

type Props = {
  connection: Connection
  interpret: InterpretResult
  onBack: () => void
  onRestart: () => void
}

type ChatItem = {
  role: 'user' | 'assistant'
  text: string
  sql?: string
  rows?: Record<string, unknown>[]
  preview?: MutatePreview
}

export function WorkbenchStep({ connection, interpret, onBack, onRestart }: Props) {
  const [mode, setMode] = useState<'query' | 'mutate'>('query')
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<ChatItem[]>([
    {
      role: 'assistant',
      text: `已就绪。当前范围：${interpret.selected_tables.join(', ')}。可以用自然语言查询或提出写入（写入需确认）。`,
    },
  ])
  const [pending, setPending] = useState<MutatePreview | null>(null)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!prompt.trim()) return
    const userText = prompt.trim()
    setPrompt('')
    setItems((prev) => [...prev, { role: 'user', text: userText }])
    setBusy(true)
    setError(null)
    try {
      if (mode === 'query') {
        const result: QueryResult = await api.nlQuery(connection.id, userText)
        setItems((prev) => [
          ...prev,
          {
            role: 'assistant',
            text:
              result.reply ||
              result.explanation ||
              (result.row_count != null
                ? `查询完成，返回 ${result.row_count} 行。`
                : '已完成。'),
            sql: result.sql || undefined,
            rows: result.rows,
          },
        ])
      } else {
        const preview = await api.nlMutate(connection.id, userText)
        setPending(preview.blocked ? null : preview)
        setItems((prev) => [
          ...prev,
          {
            role: 'assistant',
            text: preview.blocked
              ? `已拦截：${preview.block_reason}`
              : `预览 ${preview.operation} → ${preview.table}，影响约 ${preview.affected_count} 行。请确认后执行。`,
            sql: preview.sql,
            preview,
          },
        ])
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setItems((prev) => [...prev, { role: 'assistant', text: `失败：${msg}` }])
    } finally {
      setBusy(false)
    }
  }

  async function confirmPending() {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.confirmMutate(connection.id, pending.preview_id)
      setItems((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: `已执行 ${result.operation}，rowcount=${result.rowcount}`,
          sql: result.sql,
        },
      ])
      setPending(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel workbench">
      <header className="panel-header">
        <h2>自然语言工作台</h2>
        <p>
          {connection.name} · 范围 {interpret.selected_tables.join(', ')}
        </p>
      </header>

      <div className="mode-switch">
        <button
          type="button"
          className={`btn ${mode === 'query' ? 'primary' : 'ghost'}`}
          onClick={() => setMode('query')}
        >
          查询
        </button>
        <button
          type="button"
          className={`btn ${mode === 'mutate' ? 'primary' : 'ghost'}`}
          onClick={() => setMode('mutate')}
        >
          写入（需确认）
        </button>
      </div>

      <div className="chat">
        {items.map((item, idx) => (
          <article key={idx} className={`bubble ${item.role}`}>
            <p>{item.text}</p>
            {item.sql && (
              <pre className="sql">
                <code>{item.sql}</code>
              </pre>
            )}
            {item.rows && item.rows.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {Object.keys(item.rows[0]).map((k) => (
                        <th key={k}>{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {item.rows.slice(0, 50).map((row, i) => (
                      <tr key={i}>
                        {Object.keys(item.rows![0]).map((k) => (
                          <td key={k}>{formatCell(row[k])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>
        ))}
      </div>

      {pending && (
        <div className="confirm-bar">
          <span>
            待确认：{pending.operation} {pending.table}（{pending.affected_count} 行）
          </span>
          <div className="actions">
            <button
              type="button"
              className="btn ghost"
              disabled={busy}
              onClick={() => setPending(null)}
            >
              取消
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={confirmPending}
            >
              确认执行
            </button>
          </div>
        </div>
      )}

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={
            mode === 'query'
              ? '例如：查出所有 TODO 状态的任务'
              : '例如：给负责人 1 新建一个高优先级任务叫修复超时'
          }
          rows={3}
        />
        <button className="btn primary" type="submit" disabled={busy || !prompt.trim()}>
          {busy ? '处理中…' : '发送'}
        </button>
      </form>

      {error && <p className="err">{error}</p>}

      <div className="actions spread">
        <button type="button" className="btn ghost" onClick={onBack}>
          返回解读
        </button>
        <button type="button" className="btn ghost" onClick={onRestart}>
          重新选择连接
        </button>
      </div>
    </section>
  )
}

function formatCell(v: unknown) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
