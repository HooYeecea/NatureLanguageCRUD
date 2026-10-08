import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import { heuristicTableAlias } from '../tableAlias'
import type { Connection, GlossaryTable, SchemaOverview, TableInfo } from '../types'

type Selected = { table: string; schema_name?: string | null }

type Props = {
  connection: Connection
  onBack: () => void
  onNext: (schema: SchemaOverview, selected: Selected[]) => void
}

export function SelectTablesStep({ connection, onBack, onNext }: Props) {
  const [schema, setSchema] = useState<SchemaOverview | null>(null)
  const [glossary, setGlossary] = useState<GlossaryTable[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setBusy(true)
    Promise.all([
      api.getSchema(connection.id),
      api.getGlossary(connection.id).catch(() => ({ tables: [] as GlossaryTable[] })),
    ])
      .then(([s, g]) => {
        setSchema(s)
        setGlossary(g.tables || [])
        if (s.tables.length <= 8) {
          setSelected(new Set(s.tables.map((t) => keyOf(t.schema_name, t.name))))
        }
      })
      .catch((e) => setError(friendlyError(e)))
      .finally(() => setBusy(false))
  }, [connection.id])

  const tables = schema?.tables || []

  const aliasMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const t of tables) {
      map.set(keyOf(t.schema_name, t.name), heuristicTableAlias(t.name))
    }
    for (const g of glossary) {
      const k = keyOf(g.schema_name, g.table)
      if (g.alias) map.set(k, g.alias)
    }
    return map
  }, [tables, glossary])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tables
    return tables.filter((t) => {
      const k = keyOf(t.schema_name, t.name)
      const alias = aliasMap.get(k) || ''
      const syn =
        glossary
          .find((g) => g.table === t.name && (g.schema_name || null) === (t.schema_name || null))
          ?.synonyms?.join(' ') || ''
      const hay = [
        t.name,
        t.schema_name || '',
        alias,
        syn,
        t.columns.map((c) => c.name).join(' '),
      ]
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [tables, query, aliasMap, glossary])

  const groups = useMemo(() => groupBySchema(filtered), [filtered])

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

  function selectVisible() {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const t of filtered) next.add(keyOf(t.schema_name, t.name))
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
      setError(friendlyError(e))
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
          NL 操作仅在此范围内。支持按表名、中文别名或字段名搜索。
        </p>
      </header>

      {busy && !schema && <p className="muted">正在读取 schema…</p>}
      {error && <p className="err">{error}</p>}

      {schema && (
        <>
          <div className="toolbar table-toolbar">
            <div className="table-search-wrap">
              <input
                className="table-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索表名 / 别名 / 字段，例如：角色、sys_role"
              />
              <span className="muted small">
                显示 {filtered.length} / {tables.length} · 已选 {selectedList.length}
              </span>
            </div>
            <div className="actions">
              <button type="button" className="btn ghost" onClick={selectVisible}>
                选中当前
              </button>
              <button type="button" className="btn ghost" onClick={selectAll}>
                全选
              </button>
              <button type="button" className="btn ghost" onClick={clearAll}>
                清空
              </button>
            </div>
          </div>

          {filtered.length === 0 ? (
            <p className="muted">没有匹配的表，试试别的关键词。</p>
          ) : (
            <div className="schema-groups">
              {groups.map(([schemaName, groupTables]) => (
                <div key={schemaName || '__default__'} className="schema-group">
                  <h3 className="schema-heading">
                    {schemaName || '默认 schema'}
                    <span className="muted small"> · {groupTables.length} 张</span>
                  </h3>
                  <ul className="table-list">
                    {groupTables.map((t) => {
                      const k = keyOf(t.schema_name, t.name)
                      const checked = selected.has(k)
                      const alias = aliasMap.get(k)
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
                              {alias && alias !== t.name && (
                                <span className="alias-chip">{alias}</span>
                              )}
                              <div className="muted small">
                                {t.columns.length} 字段
                                {t.primary_key.length > 0 && ` · PK: ${t.primary_key.join(', ')}`}
                                {t.foreign_keys.length > 0 && ` · FK: ${t.foreign_keys.length}`}
                              </div>
                            </div>
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              ))}
            </div>
          )}
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

function groupBySchema(tables: TableInfo[]): Array<[string | null, TableInfo[]]> {
  const map = new Map<string | null, TableInfo[]>()
  for (const t of tables) {
    const key = t.schema_name ?? null
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(t)
  }
  return Array.from(map.entries()).sort(([a], [b]) => {
    if (a === null) return -1
    if (b === null) return 1
    return a.localeCompare(b)
  })
}
