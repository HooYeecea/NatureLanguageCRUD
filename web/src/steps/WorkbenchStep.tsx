import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import type { PersistedChatItem } from '../session'
import type { ChatTurn, Connection, InterpretResult, MutatePreview, QueryResult } from '../types'

type Props = {
  connection: Connection
  interpret: InterpretResult
  initialItems?: PersistedChatItem[]
  initialMode?: 'query' | 'mutate'
  onChatChange?: (items: PersistedChatItem[]) => void
  onModeChange?: (mode: 'query' | 'mutate') => void
  onBack: () => void
  onRestart: () => void
}

type ChatItem = PersistedChatItem & {
  preview?: MutatePreview
}

function defaultWelcome(interpret: InterpretResult): ChatItem {
  return {
    role: 'assistant',
    kind: 'text',
    text: `已就绪。当前范围：${interpret.selected_tables.join(', ')}。可以用自然语言查询或提出写入（写入需确认）。支持追问；大批量写入需二次确认；执行后会回读变更行。`,
  }
}

export function WorkbenchStep({
  connection,
  interpret,
  initialItems,
  initialMode = 'query',
  onChatChange,
  onModeChange,
  onBack,
  onRestart,
}: Props) {
  const [mode, setMode] = useState<'query' | 'mutate'>(initialMode)
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<ChatItem[]>(
    initialItems && initialItems.length > 0 ? initialItems : [defaultWelcome(interpret)],
  )
  const [pending, setPending] = useState<MutatePreview | null>(null)
  const [ackLarge, setAckLarge] = useState(false)
  const chatRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = chatRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items, busy])

  useEffect(() => {
    onChatChange?.(
      items.map(({ preview: _p, ...rest }) => rest),
    )
  }, [items, onChatChange])

  function setModeAndNotify(next: 'query' | 'mutate') {
    setMode(next)
    onModeChange?.(next)
  }

  function appendQueryResult(result: QueryResult, sqlLabel: string) {
    const next: ChatItem[] = []
    if (result.sql) {
      next.push({
        role: 'assistant',
        kind: 'sql',
        text: sqlLabel,
        sql: result.sql,
        editableSql: true,
        originalSql: result.original_sql,
        validationOk: result.validation_ok,
        validationNote: result.validation_note,
      })
    }
    if (result.rows && result.rows.length > 0) {
      next.push({
        role: 'assistant',
        kind: 'result',
        text: `查询结果（${result.row_count} 行）`,
        rows: result.rows,
        rowCount: result.row_count,
      })
    } else if (result.sql) {
      next.push({
        role: 'assistant',
        kind: 'result',
        text: result.reply || result.explanation || '查询完成，没有返回数据。',
        rowCount: result.row_count ?? 0,
      })
    } else {
      next.push({
        role: 'assistant',
        kind: 'text',
        text: result.reply || result.explanation || '未生成可执行查询。',
      })
    }
    return next
  }

  function buildHistory(): ChatTurn[] {
    const turns: ChatTurn[] = []
    for (const item of items) {
      if (item.role === 'user' && item.kind === 'text' && item.text && item.text !== '执行编辑后的 SQL') {
        turns.push({ role: 'user', content: item.text })
      } else if (item.sql) {
        turns.push({ role: 'assistant', content: item.text || '', sql: item.sql })
      }
    }
    return turns.slice(-8)
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!prompt.trim()) return
    const userText = prompt.trim()
    const history = buildHistory()
    setPrompt('')
    setItems((prev) => [...prev, { role: 'user', kind: 'text', text: userText }])
    setBusy(true)
    setError(null)
    setAckLarge(false)
    try {
      if (mode === 'query') {
        const result: QueryResult = await api.nlQuery(connection.id, userText, { history })
        const label = result.retried ? '已校验并改写 SQL' : '已转换为 SQL'
        setItems((prev) => [...prev, ...appendQueryResult(result, label)])
      } else {
        const preview = await api.nlMutate(connection.id, userText, history)
        setPending(preview.blocked ? null : preview)
        const next: ChatItem[] = []
        if (preview.sql) {
          next.push({
            role: 'assistant',
            kind: 'sql',
            text: '已生成写入 SQL（待确认）',
            sql: preview.sql,
          })
        }
        if (preview.sample_rows?.length) {
          next.push({
            role: 'assistant',
            kind: 'result',
            text:
              preview.operation === 'delete'
                ? `将删除的样例行（${preview.sample_rows.length}）`
                : `将影响的样例行（${preview.sample_rows.length}）`,
            rows: preview.sample_rows,
            rowCount: preview.sample_rows.length,
          })
        }
        next.push({
          role: 'assistant',
          kind: 'text',
          text: preview.blocked
            ? `已拦截：${friendlyError(preview.block_reason, '该写入被策略拦截')}`
            : preview.requires_ack
              ? `预览 ${preview.operation} → ${preview.table}，影响约 ${preview.affected_count} 行（≥${preview.ack_threshold}，需二次确认）。`
              : `预览 ${preview.operation} → ${preview.table}，影响约 ${preview.affected_count} 行。请确认后执行。`,
          preview,
        })
        setItems((prev) => [...prev, ...next])
      }
    } catch (err) {
      const msg = friendlyError(err)
      setError(msg)
      setItems((prev) => [
        ...prev,
        { role: 'assistant', kind: 'error', text: msg },
      ])
    } finally {
      setBusy(false)
    }
  }

  async function runEditedSql(sql: string) {
    const trimmed = sql.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    setItems((prev) => [
      ...prev,
      { role: 'user', kind: 'text', text: '执行编辑后的 SQL' },
    ])
    try {
      const result = await api.runSql(connection.id, trimmed)
      setItems((prev) => [...prev, ...appendQueryResult(result, '已执行编辑后的 SQL')])
    } catch (err) {
      const msg = friendlyError(err)
      setError(msg)
      setItems((prev) => [...prev, { role: 'assistant', kind: 'error', text: msg }])
    } finally {
      setBusy(false)
    }
  }

  async function confirmPending() {
    if (!pending) return
    if (pending.requires_ack && !ackLarge) {
      setError(`影响约 ${pending.affected_count} 行，请先勾选二次确认。`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await api.confirmMutate(connection.id, pending.preview_id, {
        ack_large_impact: ackLarge || !pending.requires_ack,
      })
      const next: ChatItem[] = [
        {
          role: 'assistant',
          kind: 'text',
          text: `已执行 ${result.operation}，影响 ${result.rowcount} 行。`,
          sql: result.sql,
        },
      ]
      if (result.changed_rows && result.changed_rows.length > 0) {
        next.push({
          role: 'assistant',
          kind: 'result',
          text:
            result.operation === 'delete'
              ? `已删除的行（回读前快照，${result.changed_rows.length}）`
              : `变更后回读（${result.changed_rows.length} 行）`,
          rows: result.changed_rows,
          rowCount: result.changed_rows.length,
        })
      }
      setItems((prev) => [...prev, ...next])
      setPending(null)
      setAckLarge(false)
    } catch (err) {
      setError(friendlyError(err))
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
          onClick={() => setModeAndNotify('query')}
        >
          查询
        </button>
        <button
          type="button"
          className={`btn ${mode === 'mutate' ? 'primary' : 'ghost'}`}
          onClick={() => setModeAndNotify('mutate')}
        >
          写入（需确认）
        </button>
      </div>

      <div className="chat" ref={chatRef}>
        {items.map((item, idx) => (
          <article key={idx} className={`bubble ${item.role} ${item.kind || ''}`}>
            <p>{item.text}</p>
            {item.sql && item.editableSql ? (
              <EditableSql sql={item.sql} disabled={busy} onRun={runEditedSql} />
            ) : (
              item.sql && (
                <pre className="sql">
                  <code>{item.sql}</code>
                </pre>
              )
            )}
            {item.originalSql && item.originalSql !== item.sql && (
              <p className="muted sql-original">初次 SQL：{item.originalSql}</p>
            )}
            {item.validationNote && (
              <p className={`validation-note ${item.validationOk === false ? 'warn' : ''}`}>
                {item.validationOk === false ? '校验提醒：' : '校验：'}
                {item.validationNote}
              </p>
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
        {busy && (
          <article className="bubble assistant text">
            <p className="muted">正在处理…</p>
          </article>
        )}
      </div>

      {pending && (
        <div className={`confirm-bar ${pending.requires_ack ? 'warn' : ''}`}>
          <div className="confirm-copy">
            <span>
              待确认：{pending.operation} {pending.table}（{pending.affected_count} 行）
            </span>
            {pending.requires_ack && (
              <label className="ack-label">
                <input
                  type="checkbox"
                  checked={ackLarge}
                  onChange={(e) => setAckLarge(e.target.checked)}
                  disabled={busy}
                />
                我确认影响约 {pending.affected_count} 行（≥{pending.ack_threshold}）
              </label>
            )}
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn ghost"
              disabled={busy}
              onClick={() => {
                setPending(null)
                setAckLarge(false)
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || (pending.requires_ack && !ackLarge)}
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
              ? '例如：查出所有 TODO 状态的任务。也可以追问：只看第一条 / 再按权限过滤'
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

function EditableSql({
  sql,
  disabled,
  onRun,
}: {
  sql: string
  disabled: boolean
  onRun: (sql: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(sql)

  useEffect(() => {
    setDraft(sql)
    setEditing(false)
  }, [sql])

  return (
    <div className="sql-block">
      {editing ? (
        <textarea
          className="sql-editor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={6}
          spellCheck={false}
        />
      ) : (
        <pre className="sql">
          <code>{sql}</code>
        </pre>
      )}
      <div className="sql-toolbar">
        {editing ? (
          <>
            <button
              type="button"
              className="btn ghost"
              disabled={disabled}
              onClick={() => {
                setDraft(sql)
                setEditing(false)
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={disabled || !draft.trim()}
              onClick={() => onRun(draft)}
            >
              执行 SQL
            </button>
          </>
        ) : (
          <button type="button" className="btn ghost" disabled={disabled} onClick={() => setEditing(true)}>
            编辑并执行
          </button>
        )}
      </div>
    </div>
  )
}

function formatCell(v: unknown) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
