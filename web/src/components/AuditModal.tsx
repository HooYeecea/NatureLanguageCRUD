import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import type { AuditLog, Connection } from '../types'

type Props = {
  open: boolean
  onClose: () => void
  connectionId?: string | null
  connections?: Connection[]
}

const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: 'success', value: 'success' },
  { label: 'error', value: 'error' },
  { label: 'blocked', value: 'blocked' },
]

export function AuditModal({ open, onClose, connectionId, connections = [] }: Props) {
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filterConn, setFilterConn] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterAction, setFilterAction] = useState('')
  const [selected, setSelected] = useState<AuditLog | null>(null)

  const connName = useMemo(() => {
    const map = new Map(connections.map((c) => [c.id, c.name]))
    return (id?: string | null) => (id ? map.get(id) || id.slice(0, 8) : '—')
  }, [connections])

  useEffect(() => {
    if (!open) return
    setFilterConn(connectionId || '')
    setSelected(null)
  }, [open, connectionId])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setBusy(true)
    setError(null)
    api
      .listAudit({
        connection_id: filterConn || undefined,
        status: filterStatus || undefined,
        action: filterAction || undefined,
        limit: 100,
      })
      .then((rows) => {
        if (!cancelled) {
          setLogs(rows)
          setSelected((prev) => (prev ? rows.find((r) => r.id === prev.id) || null : null))
        }
      })
      .catch((e) => {
        if (!cancelled) setError(friendlyError(e))
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, filterConn, filterStatus, filterAction])

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal audit-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="审计日志"
      >
        <header className="modal-header">
          <div>
            <h2>审计日志</h2>
            <p className="muted small">查看查询 / 写入 / 策略等操作记录</p>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="audit-filters">
          <select value={filterConn} onChange={(e) => setFilterConn(e.target.value)}>
            <option value="">全部连接</option>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || 'all'} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            placeholder="动作，如 query.nl / mutate.confirm"
          />
          <button
            type="button"
            className="btn ghost"
            disabled={busy}
            onClick={() => {
              setFilterAction('')
              setFilterStatus('')
              setFilterConn(connectionId || '')
            }}
          >
            重置
          </button>
        </div>

        {error && <p className="err">{error}</p>}
        {busy && <p className="muted">加载中…</p>}

        <div className="audit-layout">
          <ul className="audit-list">
            {logs.length === 0 && !busy && <li className="muted">暂无审计记录</li>}
            {logs.map((log) => (
              <li key={log.id}>
                <button
                  type="button"
                  className={`audit-item ${selected?.id === log.id ? 'on' : ''}`}
                  onClick={() => setSelected(log)}
                >
                  <div className="audit-item-top">
                    <span className={`status-pill ${log.status}`}>{log.status}</span>
                    <span className="muted small">{formatTime(log.created_at)}</span>
                  </div>
                  <strong>{log.action}</strong>
                  <div className="muted small">{log.summary || '—'}</div>
                  <div className="muted small">连接：{connName(log.connection_id)}</div>
                </button>
              </li>
            ))}
          </ul>

          <div className="audit-detail">
            {!selected ? (
              <p className="muted">点击左侧记录查看 SQL、影响行等详情。</p>
            ) : (
              <>
                <h3>{selected.action}</h3>
                <p className="muted small">
                  {formatTime(selected.created_at)} · {selected.status} ·{' '}
                  {connName(selected.connection_id)}
                </p>
                {selected.summary && <p>{selected.summary}</p>}
                {typeof selected.detail?.sql === 'string' && selected.detail.sql && (
                  <>
                    <h4>SQL</h4>
                    <pre className="sql">
                      <code>{selected.detail.sql}</code>
                    </pre>
                  </>
                )}
                <h4>影响 / 结果</h4>
                <ul className="audit-meta">
                  {selected.detail?.row_count != null && (
                    <li>返回行数：{String(selected.detail.row_count)}</li>
                  )}
                  {selected.detail?.affected_count != null && (
                    <li>影响行数：{String(selected.detail.affected_count)}</li>
                  )}
                  {selected.detail?.rowcount != null && (
                    <li>执行影响：{String(selected.detail.rowcount)}</li>
                  )}
                  {selected.detail?.changed_row_count != null && (
                    <li>回读行数：{String(selected.detail.changed_row_count)}</li>
                  )}
                  {selected.detail?.operation != null && (
                    <li>操作：{String(selected.detail.operation)}</li>
                  )}
                  {selected.detail?.table != null && (
                    <li>表：{String(selected.detail.table)}</li>
                  )}
                  {selected.detail?.prompt != null && (
                    <li>问题：{String(selected.detail.prompt)}</li>
                  )}
                  {selected.detail?.blocked === true && (
                    <li>已拦截：{String(selected.detail.block_reason || '是')}</li>
                  )}
                </ul>
                <h4>原始详情</h4>
                <pre className="audit-json">
                  <code>{JSON.stringify(selected.detail || {}, null, 2)}</code>
                </pre>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}
