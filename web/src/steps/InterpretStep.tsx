import { useEffect, useState } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import type { Connection, InterpretResult } from '../types'

type Selected = { table: string; schema_name?: string | null }

type Props = {
  connection: Connection
  selectedTables: Selected[]
  onBack: () => void
  onNext: (result: InterpretResult) => void
}

export function InterpretStep({
  connection,
  selectedTables,
  onBack,
  onNext,
}: Props) {
  const [result, setResult] = useState<InterpretResult | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [llmConfigured, setLlmConfigured] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function run() {
      setBusy(true)
      setError(null)
      try {
        const settings = await api.settings()
        if (!cancelled) setLlmConfigured(settings.llm_configured)
        const interpreted = await api.interpret(
          connection.id,
          selectedTables,
          true,
        )
        if (!cancelled) setResult(interpreted)
      } catch (e) {
        if (!cancelled) setError(friendlyError(e))
      } finally {
        if (!cancelled) setBusy(false)
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [connection.id, selectedTables])

  return (
    <section className="panel">
      <header className="panel-header">
        <h2>模型解读表结构与关系</h2>
        <p>
          先用引擎读取真实 schema，再让大模型补充业务含义与关联说明。
          {!llmConfigured && ' 当前未配置 LLM，将仅展示元数据关系。'}
        </p>
      </header>

      {busy && <p className="muted">正在分析所选表…</p>}
      {error && <p className="err">{error}</p>}

      {result && (
        <div className="interpret">
          <div className="badge-row">
            <span className="badge">{result.source === 'llm' ? 'LLM' : 'Metadata'}</span>
            <span className="muted">
              表：{result.selected_tables.join(', ')}
            </span>
          </div>

          <p className="overview">{result.overview}</p>

          <h3>表说明</h3>
          <ul className="cards">
            {result.tables.map((t) => (
              <li key={t.name}>
                <strong>{t.name}</strong>
                <p>{t.purpose}</p>
                {t.key_fields?.length > 0 && (
                  <div className="muted small">关键字段：{t.key_fields.join(', ')}</div>
                )}
              </li>
            ))}
          </ul>

          <h3>表关系</h3>
          {result.relationships.length === 0 ? (
            <p className="muted">暂无明显关系。</p>
          ) : (
            <ul className="rel-list">
              {result.relationships.map((r, i) => (
                <li key={`${r.from_table}-${r.to_table}-${i}`}>
                  <strong>
                    {r.from_table} → {r.to_table}
                  </strong>
                  <div className="muted small">{r.via}</div>
                  {r.note && <div>{r.note}</div>}
                </li>
              ))}
            </ul>
          )}

          {result.join_hints.length > 0 && (
            <>
              <h3>建议 Join</h3>
              <ul className="code-list">
                {result.join_hints.map((h) => (
                  <li key={h}>
                    <code>{h}</code>
                  </li>
                ))}
              </ul>
            </>
          )}

          {result.warnings.length > 0 && (
            <>
              <h3>注意</h3>
              <ul className="warn-list">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      <div className="actions spread">
        <button type="button" className="btn ghost" onClick={onBack}>
          上一步
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={!result || busy}
          onClick={() => result && onNext(result)}
        >
          进入工作台
        </button>
      </div>
    </section>
  )
}
