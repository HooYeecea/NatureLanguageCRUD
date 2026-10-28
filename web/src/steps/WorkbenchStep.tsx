import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api } from '../api'
import { downloadCsv } from '../csv'
import { friendlyError } from '../errors'
import type { PersistedChatItem } from '../session'
import { emptyResultHints } from '../emptyHints'
import { suggestPrompts } from '../suggestPrompts'
import type {
  ChatTurn,
  Connection,
  GlossaryTable,
  InterpretResult,
  MutateExecuteResult,
  MutatePreview,
  QueryResult,
} from '../types'

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
  const [glossary, setGlossary] = useState<GlossaryTable[]>([])
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

  useEffect(() => {
    api
      .getGlossary(connection.id)
      .then((g) => setGlossary(g.tables || []))
      .catch(() => setGlossary([]))
  }, [connection.id])

  const prompts = useMemo(
    () => suggestPrompts(mode, interpret.selected_tables || [], glossary),
    [mode, interpret.selected_tables, glossary],
  )

  function setModeAndNotify(next: 'query' | 'mutate') {
    setMode(next)
    onModeChange?.(next)
  }

  function appendQueryResult(
    result: QueryResult,
    sqlLabel: string,
    userPrompt?: string,
  ) {
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
      const truncNote =
        result.truncated && result.limit
          ? ` · 已截断至 LIMIT ${result.limit}，可能还有更多行`
          : ''
      next.push({
        role: 'assistant',
        kind: 'result',
        text: `查询结果（${result.row_count} 行）${truncNote}`,
        rows: result.rows,
        rowCount: result.row_count,
        truncated: !!result.truncated,
        limit: result.limit,
      })
    } else if (result.sql) {
      const hints = emptyResultHints({
        userPrompt,
        sql: result.sql,
        tables: result.tables,
        limit: result.limit ?? undefined,
      })
      next.push({
        role: 'assistant',
        kind: 'result',
        text: result.reply || result.explanation || '查询完成，没有返回数据。',
        rowCount: result.row_count ?? 0,
        emptyHints: hints,
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
    const skipUser = new Set(['执行编辑后的 SQL', '重新预览编辑后的写入 SQL'])
    for (const item of items) {
      if (item.role === 'user' && item.kind === 'text' && item.text && !skipUser.has(item.text)) {
        turns.push({ role: 'user', content: item.text })
      } else if (item.sql) {
        turns.push({ role: 'assistant', content: item.text || '', sql: item.sql })
      }
    }
    return turns.slice(-8)
  }

  function appendMutatePreview(preview: MutatePreview, sqlLabel: string): ChatItem[] {
    const next: ChatItem[] = []
    if (preview.sql) {
      next.push({
        role: 'assistant',
        kind: 'sql',
        text: sqlLabel,
        sql: preview.sql,
        editableSql: !preview.blocked,
        mutatePreviewId: preview.blocked ? undefined : preview.preview_id,
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
    return next
  }

  async function sendPrompt(raw: string) {
    const userText = raw.trim()
    if (!userText || busy) return
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
        setItems((prev) => [...prev, ...appendQueryResult(result, label, userText)])
      } else {
        const preview = await api.nlMutate(connection.id, userText, history)
        setPending(preview.blocked ? null : preview)
        setItems((prev) => [...prev, ...appendMutatePreview(preview, '已生成写入 SQL（待确认）')])
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

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    await sendPrompt(prompt)
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

  async function reviseEditedMutate(previewId: string, sql: string) {
    const trimmed = sql.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    setAckLarge(false)
    setItems((prev) => [
      ...prev,
      { role: 'user', kind: 'text', text: '重新预览编辑后的写入 SQL' },
    ])
    try {
      const preview = await api.reviseMutate(connection.id, previewId, trimmed)
      setPending(preview.blocked ? null : preview)
      setItems((prev) => [...prev, ...appendMutatePreview(preview, '已根据编辑后的 SQL 重新生成预览')])
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
      setItems((prev) => [...prev, ...appendMutateResult(result)])
      setPending(null)
      setAckLarge(false)
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setBusy(false)
    }
  }

  async function cancelPending() {
    if (!pending) return
    const previewId = pending.preview_id
    setBusy(true)
    setError(null)
    try {
      await api.cancelMutate(connection.id, previewId)
      setPending(null)
      setAckLarge(false)
      setItems((prev) => [
        ...prev,
        { role: 'assistant', kind: 'text', text: '已取消该写入预览。' },
      ])
    } catch (err) {
      // Still clear local pending so the bar does not stick after expiry/cancel races.
      setPending(null)
      setAckLarge(false)
      setError(friendlyError(err))
    } finally {
      setBusy(false)
    }
  }

  function appendMutateResult(result: MutateExecuteResult): ChatItem[] {
    const next: ChatItem[] = [
      {
        role: 'assistant',
        kind: 'text',
        text: `已执行 ${result.operation}，影响 ${result.rowcount} 行。`,
        sql: result.sql,
      },
    ]
    const before = result.before_rows || []
    const after = result.changed_rows || []
    if (result.operation === 'update' && (before.length > 0 || after.length > 0)) {
      next.push({
        role: 'assistant',
        kind: 'diff',
        text: `变更对比（${Math.max(before.length, after.length)} 行）`,
        beforeRows: before,
        afterRows: after,
        diffOperation: result.operation,
        rowCount: Math.max(before.length, after.length),
      })
    } else if (after.length > 0 || before.length > 0) {
      const rows = result.operation === 'delete' ? before : after
      next.push({
        role: 'assistant',
        kind: 'result',
        text:
          result.operation === 'delete'
            ? `已删除的行（回读前快照，${rows.length}）`
            : `变更后回读（${rows.length} 行）`,
        rows,
        rowCount: rows.length,
        undoId: result.undo_id || undefined,
      })
    }
    return next
  }

  async function undoDelete(undoId: string) {
    setBusy(true)
    setError(null)
    try {
      const restored = await api.undoMutate(connection.id, undoId)
      setItems((prev) => [
        ...prev.map((item) =>
          item.undoId === undoId ? { ...item, undoId: undefined } : item,
        ),
        {
          role: 'assistant',
          kind: 'text',
          text: `已撤销删除，恢复 ${restored.restored_count} 行到 ${restored.table}。`,
        },
        ...(restored.rows.length
          ? [
              {
                role: 'assistant' as const,
                kind: 'result' as const,
                text: `恢复后的行（${restored.rows.length}）`,
                rows: restored.rows,
                rowCount: restored.rows.length,
              },
            ]
          : []),
      ])
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
              <EditableSql
                sql={item.sql}
                disabled={
                  busy ||
                  (!!item.mutatePreviewId &&
                    (!pending || pending.preview_id !== item.mutatePreviewId))
                }
                editLabel={item.mutatePreviewId ? '编辑并重新预览' : '编辑并执行'}
                runLabel={item.mutatePreviewId ? '重新预览' : '执行 SQL'}
                onRun={(sql) => {
                  if (item.mutatePreviewId) {
                    void reviseEditedMutate(item.mutatePreviewId, sql)
                  } else {
                    void runEditedSql(sql)
                  }
                }}
              />
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
            {item.kind === 'diff' && (
              <MutateDiff
                beforeRows={item.beforeRows || []}
                afterRows={item.afterRows || []}
              />
            )}
            {item.emptyHints && item.emptyHints.length > 0 && (
              <div className="suggest-prompts empty-hints" aria-label="空结果建议">
                <span className="muted small">没有数据，可以试试：</span>
                {item.emptyHints.map((h) => (
                  <button
                    key={h}
                    type="button"
                    className="suggest-chip"
                    disabled={busy}
                    onClick={() => void sendPrompt(h)}
                  >
                    {h}
                  </button>
                ))}
              </div>
            )}
            {item.rows && item.rows.length > 0 && (
              <div className="result-block">
                <div className="result-toolbar">
                  {item.truncated && (
                    <span className="trunc-note">
                      结果已截断{item.limit ? `（LIMIT ${item.limit}）` : ''}，导出仅为当前返回行
                    </span>
                  )}
                  {item.undoId && (
                    <button
                      type="button"
                      className="btn ghost"
                      disabled={busy}
                      onClick={() => void undoDelete(item.undoId!)}
                    >
                      撤销删除
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={busy}
                    onClick={() =>
                      downloadCsv(
                        `query-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`,
                        item.rows!,
                      )
                    }
                  >
                    导出 CSV
                  </button>
                </div>
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
                {item.rows.length > 50 && (
                  <p className="muted small">界面仅预览前 50 行，导出包含全部 {item.rows.length} 行。</p>
                )}
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
              onClick={() => void cancelPending()}
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

      {prompts.length > 0 && !pending && (
        <div className="suggest-prompts" aria-label="推荐问法">
          <span className="muted small">试试（点击直接发送）：</span>
          {prompts.map((p) => (
            <button
              key={p}
              type="button"
              className="suggest-chip"
              disabled={busy}
              onClick={() => void sendPrompt(p)}
            >
              {p}
            </button>
          ))}
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
  editLabel = '编辑并执行',
  runLabel = '执行 SQL',
}: {
  sql: string
  disabled: boolean
  onRun: (sql: string) => void
  editLabel?: string
  runLabel?: string
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
              {runLabel}
            </button>
          </>
        ) : (
          <button type="button" className="btn ghost" disabled={disabled} onClick={() => setEditing(true)}>
            {editLabel}
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

function MutateDiff({
  beforeRows,
  afterRows,
}: {
  beforeRows: Record<string, unknown>[]
  afterRows: Record<string, unknown>[]
}) {
  const count = Math.max(beforeRows.length, afterRows.length, 1)
  const columns = Array.from(
    new Set([
      ...beforeRows.flatMap((r) => Object.keys(r)),
      ...afterRows.flatMap((r) => Object.keys(r)),
    ]),
  )
  if (columns.length === 0) {
    return <p className="muted small">没有可对比的字段。</p>
  }

  return (
    <div className="diff-block">
      {Array.from({ length: Math.min(count, 20) }, (_, i) => {
        const before = beforeRows[i] || {}
        const after = afterRows[i] || {}
        return (
          <div key={i} className="diff-row-card">
            <div className="diff-side">
              <p className="diff-label">变更前</p>
              <dl className="diff-fields">
                {columns.map((col) => {
                  const changed = !valuesEqual(before[col], after[col])
                  return (
                    <div key={`b-${col}`} className={changed ? 'changed' : ''}>
                      <dt>{col}</dt>
                      <dd>{formatCell(before[col])}</dd>
                    </div>
                  )
                })}
              </dl>
            </div>
            <div className="diff-side">
              <p className="diff-label">变更后</p>
              <dl className="diff-fields">
                {columns.map((col) => {
                  const changed = !valuesEqual(before[col], after[col])
                  return (
                    <div key={`a-${col}`} className={changed ? 'changed after' : ''}>
                      <dt>{col}</dt>
                      <dd>{formatCell(after[col])}</dd>
                    </div>
                  )
                })}
              </dl>
            </div>
          </div>
        )
      })}
      {count > 20 && (
        <p className="muted small">仅展示前 20 行对比，共 {count} 行。</p>
      )}
    </div>
  )
}

function valuesEqual(a: unknown, b: unknown) {
  if (a === b) return true
  if (a == null && b == null) return true
  return JSON.stringify(a) === JSON.stringify(b)
}
