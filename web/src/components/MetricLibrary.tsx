import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Metric } from '../types'

const blank = { name: '', table: '', schema_name: '', definition: '', time_field: '' }
export function MetricLibrary({ connectionId }: {connectionId: string}) {
  const [items, setItems] = useState<Metric[]>([])
  const [canEdit, setCanEdit] = useState(false)
  const [draft, setDraft] = useState(blank)
  const [editing, setEditing] = useState<string>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = useCallback(async () => {
    const data = await api.metrics(connectionId)
    setItems(data.items); setCanEdit(data.can_edit)
  }, [connectionId])
  useEffect(() => { void reload().catch(e => setError(String(e))) }, [reload])
  async function save() {
    setBusy(true); setError('')
    try {
      await api.saveMetric(connectionId, {...draft, schema_name: draft.schema_name || null}, editing)
      setDraft(blank); setEditing(undefined); await reload()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <details className="feature-library">
    <summary>指标口径库 · {items.length} 项</summary>
    <p className="muted small">统一统计含义、过滤条件和时间字段。自然语言查询会参考当前可读取表的口径，生成后仍需核对 SQL 与解释。</p>
    {error && <p role="alert">{error}</p>}
    {items.map(m => <article key={m.id} className="feature-entry">
      <strong>{m.name}</strong><p>{m.definition}</p>
      <small>{m.schema_name ? m.schema_name + '.' : ''}{m.table}{m.time_field ? ' · 时间字段 ' + m.time_field : ''}</small>
      {canEdit && <div className="result-toolbar">
        <button type="button" className="btn ghost" disabled={busy} onClick={() => {setEditing(m.id); setDraft({...m, schema_name: m.schema_name || ''})}}>编辑</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={async () => {
          if (!window.confirm('删除此指标口径？')) return
          setBusy(true)
          try { await api.deleteMetric(connectionId, m.id); await reload() } catch(e) {setError(String(e))} finally {setBusy(false)}
        }}>删除</button>
      </div>}
    </article>)}
    {!items.length && <p className="muted">尚未定义指标口径。</p>}
    {canEdit && <div className="feature-form">
      <label>指标名称<input value={draft.name} maxLength={80} placeholder="净销售额" onChange={e => setDraft({...draft, name: e.target.value})}/></label>
      <label>数据库表名<input value={draft.table} maxLength={128} placeholder="orders" onChange={e => setDraft({...draft, table: e.target.value})}/></label>
      <label>Schema，可留空<input value={draft.schema_name} maxLength={128} onChange={e => setDraft({...draft, schema_name: e.target.value})}/></label>
      <label>时间字段，可留空<input value={draft.time_field} maxLength={128} placeholder="paid_at" onChange={e => setDraft({...draft, time_field: e.target.value})}/></label>
      <label>计算口径<textarea value={draft.definition} maxLength={2000} placeholder="仅统计已支付订单，金额扣除退款；按支付日期统计。请填写实际字段与状态值。" onChange={e => setDraft({...draft, definition: e.target.value})}/></label>
      <div className="result-toolbar"><button type="button" className="btn primary" disabled={busy || !draft.name.trim() || !draft.table.trim() || !draft.definition.trim()} onClick={() => void save()}>{editing ? '保存修改' : '添加口径'}</button>
      {editing && <button type="button" className="btn ghost" onClick={() => {setEditing(undefined); setDraft(blank)}}>取消编辑</button>}</div>
    </div>}
  </details>
}
