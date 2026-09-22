import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Connection, SchemaOverview } from '../types'

type Selected = { table: string; schema_name?: string | null }

type Props = {
  connection: Connection
  onBack: () => void
  onNext: (schema: SchemaOverview, selected: Selected[]) => void
}

export function SelectTablesStep({ connection, onBack, onNext }: Props) {
  const [schema, setSchema] = useState<SchemaOverview | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setBusy(true)
    api
      .getSchema(connection.id)
      .then((s) => {
        setSchema(s)
        // preselect if few tables
        if (s.tables.length <= 8) {
          setSelected(new Set(s.tables.map((t) => keyOf(t.schema_name, t.name))))
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }, [connection.id])

  const tables = schema?.tables || []

  const selectedList = useMemo(() => {
    return tables
      .filter((t) => selected.has(keyOf(t.schema_name, t.name)))
      .map((t) => ({ table: t.name, schema_name: t.schema_name }))
  }, [tables, selected])

  function toggle(schemaName: string | null | undefined, name: string) {
    const k = keyOf(schemaName, name)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  function selectAll() {
    setSelected(new Set(tables.map((t) => keyOf(t.schema_name, t.name))))
  }

  function clearAll() {
    setSelected(new Set())
  }

  async function continueNext() {
    if (!schema || selectedList.length === 0) return
    setBusy(true)
    setError(null)
    try {
      await api.selectTables(connection.id, selectedList)
      onNext(schema, selectedList)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <header className="panel-header">
        <h2>选择要操作的表</h2>
        <p>
          连接 <strong>{connection.name}</strong>（{connection.dialect}）。可多选，后续
          NL 操作仅在此范围内。
        </p>
      </header>

      {busy && !schema && <p className="muted">正在读取 schema…</p>}
      {error && <p className="err">{error}</p>}

      {schema && (
        <>
          <div className="toolbar">
            <span className="muted">
              已选 {selectedList.length} / {tables.length}
            </span>
            <div className="actions">
              <button type="button" className="btn ghost" onClick={selectAll}>
                全选
              </button>
              <button type="button" className="btn ghost" onClick={clearAll}>
                清空
              </button>
            </div>
          </div>

          <ul className="table-list">
            {tables.map((t) => {
              const k = keyOf(t.schema_name, t.name)
              const checked = selected.has(k)
              return (
                <li key={k}>
                  <label className={`table-item ${checked ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(t.schema_name, t.name)}
                    />
                    <div>
                      <strong>
                        {t.schema_name ? `${t.schema_name}.` : ''}
                        {t.name}
                      </strong>
                      <div className="muted small">
                        {t.columns.length} 字段
                        {t.primary_key.length > 0 && ` · PK: ${t.primary_key.join(', ')}`}
                        {t.foreign_keys.length > 0 &&
                          ` · FK: ${t.foreign_keys.length}`}
                      </div>
                    </div>
                  </label>
                </li>
              )
            })}
          </ul>
        </>
      )}

      <div className="actions spread">
        <button type="button" className="btn ghost" onClick={onBack}>
          上一步
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={busy || selectedList.length === 0}
          onClick={continueNext}
        >
          保存选表并继续
        </button>
      </div>
    </section>
  )
}

function keyOf(schema: string | null | undefined, name: string) {
  return `${schema || ''}::${name}`
}
