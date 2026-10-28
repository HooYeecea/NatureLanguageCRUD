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
  const [secrets, setSecrets] = useState<Record<string, Set<string>>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [allowInsert, setAllowInsert] = useState(true)
  const [allowUpdate, setAllowUpdate] = useState(true)
  const [allowDelete, setAllowDelete] = useState(false)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setBusy(true)
    Promise.all([
      api.getSchema(connection.id),
      api.getGlossary(connection.id).catch(() => ({ tables: [] as GlossaryTable[] })),
      api.getPolicy(connection.id).catch(() => null),
    ])
      .then(([s, g, policy]) => {
        setSchema(s)
        setGlossary(g.tables || [])
        const nextSelected = new Set<string>()
        const nextSecrets: Record<string, Set<string>> = {}
        if (policy?.tables?.length) {
          for (const tp of policy.tables) {
            const k = keyOf(tp.schema_name, tp.table)
            nextSelected.add(k)
            nextSecrets[k] = new Set(tp.secret_columns || [])
          }
          // Restore global write flags from current policy (OR across tables).
          setAllowInsert(policy.tables.some((t) => t.allow_insert !== false))
          setAllowUpdate(policy.tables.some((t) => t.allow_update !== false))
          setAllowDelete(policy.tables.some((t) => !!t.allow_delete))
        } else if (s.tables.length <= 8) {
          for (const t of s.tables) nextSelected.add(keyOf(t.schema_name, t.name))
        }
        setSelected(nextSelected)
        setSecrets(nextSecrets)
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
      .map((t) => {
        const k = keyOf(t.schema_name, t.name)
        return {
          table: t.name,
          schema_name: t.schema_name,
          secret_columns: Array.from(secrets[k] || []),
        }
      })
  }, [tables, selected, secrets])

  function toggle(schemaName: string | null | undefined, name: string) {
    const k = keyOf(schemaName, name)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(k)) {
        next.delete(k)
        setExpanded((e) => {
          const ne = new Set(e)
          ne.delete(k)
          return ne
        })
      } else {
        next.add(k)
      }
      return next
    })
  }

  function toggleSecret(tableKey: string, column: string) {
    setSecrets((prev) => {
      const cur = new Set(prev[tableKey] || [])
      if (cur.has(column)) cur.delete(column)
      else cur.add(column)
      return { ...prev, [tableKey]: cur }
    })
  }

  function toggleExpand(tableKey: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(tableKey)) next.delete(tableKey)
      else next.add(tableKey)
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
    setExpanded(new Set())
  }

  async function continueNext() {
    if (!schema || selectedList.length === 0) return
    setBusy(true)
    setError(null)
    try {
      await api.selectTables(connection.id, selectedList, {
        allow_select: true,
        allow_insert: allowInsert,
        allow_update: allowUpdate,
        allow_delete: allowDelete,
      })
      onNext(
        schema,
        selectedList.map(({ table, schema_name }) => ({ table, schema_name })),
      )
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
          NL 操作仅在此范围内。可开关写入权限；展开已选表可勾选<strong>敏感列</strong>。
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

          <div className="perm-bar" aria-label="写入权限">
            <span className="muted small">已选表权限：</span>
            <label className="perm-chip">
              <input type="checkbox" checked disabled />
              查询
            </label>
            <label className={`perm-chip ${allowInsert ? 'on' : ''}`}>
              <input
                type="checkbox"
                checked={allowInsert}
                onChange={(e) => setAllowInsert(e.target.checked)}
              />
              新增
            </label>
            <label className={`perm-chip ${allowUpdate ? 'on' : ''}`}>
              <input
                type="checkbox"
                checked={allowUpdate}
                onChange={(e) => setAllowUpdate(e.target.checked)}
              />
              更新
            </label>
            <label className={`perm-chip ${allowDelete ? 'on warn' : ''}`}>
              <input
                type="checkbox"
                checked={allowDelete}
                onChange={(e) => setAllowDelete(e.target.checked)}
              />
              删除
            </label>
            {allowDelete && (
              <span className="muted small">删除需预览确认，成功后短时可撤销</span>
            )}
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
                      const secretCount = (secrets[k] || new Set()).size
                      const isOpen = expanded.has(k)
                      return (
                        <li key={k}>
                          <div className={`table-item ${checked ? 'on' : ''}`}>
                            <label className="table-check">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggle(t.schema_name, t.name)}
                              />
                            </label>
                            <div className="table-item-body">
                              <div className="table-item-head">
                                <strong>
                                  {t.schema_name ? `${t.schema_name}.` : ''}
                                  {t.name}
                                </strong>
                                {alias && alias !== t.name && (
                                  <span className="alias-chip">{alias}</span>
                                )}
                                {checked && (
                                  <button
                                    type="button"
                                    className="btn ghost secret-toggle"
                                    onClick={() => toggleExpand(k)}
                                  >
                                    {isOpen ? '收起敏感列' : '敏感列'}
                                    {secretCount > 0 ? ` (${secretCount})` : ''}
                                  </button>
                                )}
                              </div>
                              <div className="muted small">
                                {t.columns.length} 字段
                                {t.primary_key.length > 0 && ` · PK: ${t.primary_key.join(', ')}`}
                                {t.foreign_keys.length > 0 && ` · FK: ${t.foreign_keys.length}`}
                              </div>
                              {checked && isOpen && (
                                <SecretColumnPicker
                                  table={t}
                                  selected={secrets[k] || new Set()}
                                  onToggle={(col) => toggleSecret(k, col)}
                                />
                              )}
                            </div>
                          </div>
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

function SecretColumnPicker({
  table,
  selected,
  onToggle,
}: {
  table: TableInfo
  selected: Set<string>
  onToggle: (column: string) => void
}) {
  if (!table.columns.length) {
    return <p className="muted small secret-hint">该表没有可配置字段。</p>
  }
  return (
    <div className="secret-picker">
      <p className="muted small secret-hint">勾选后，样例 / 查询结果 / 写入回读中显示为 [redacted]</p>
      <div className="secret-cols">
        {table.columns.map((c) => (
          <label key={c.name} className={`secret-col ${selected.has(c.name) ? 'on' : ''}`}>
            <input
              type="checkbox"
              checked={selected.has(c.name)}
              onChange={() => onToggle(c.name)}
            />
            <span>{c.name}</span>
            <span className="muted small">{c.type}</span>
          </label>
        ))}
      </div>
    </div>
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
