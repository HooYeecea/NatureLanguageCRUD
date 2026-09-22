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
  const [statusText, setStatusText] = useState('正在加载分析…')

  useEffect(() => {
    let cancelled = false
    async function run() {
      setBusy(true)
      setError(null)
      setStatusText('正在检查是否已有分析结果…')
      try {
        const settings = await api.settings()
        if (!cancelled) setLlmConfigured(settings.llm_configured)

        // Prefer cache for this table set (force=false)
        const interpreted = await api.interpret(connection.id, selectedTables, {
          use_llm: true,
          force: false,
        })
        if (!cancelled) {
          setResult(interpreted)
          setStatusText(
            interpreted.cached
              ? '已加载缓存的分析结果（可重新分析）'
              : '分析完成并已保存，可复用',
          )
        }
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

  async function reanalyze() {
    setBusy(true)
    setError(null)
    setStatusText('正在用大模型重新分析…')
    try {
      const interpreted = await api.interpret(connection.id, selectedTables, {
        use_llm: true,
        force: true,
      })
      setResult(interpreted)
      setStatusText('已重新分析并更新缓存')
    } catch (e) {
      setError(friendlyError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <header className="panel-header">
        <h2>表结构与关系解读</h2>
        <p>
          分析结果会保存，下次相同选表可直接复用；也可重新用大模型分析。
          {!llmConfigured && ' 当前未配置 LLM 时，仅基于元数据/外键生成摘要。'}
        </p>
      </header>

      {busy && <p className="muted">{statusText}</p>}
      {!busy && statusText && <p className="muted small">{statusText}</p>}
      {error && <p className="err">{error}</p>}

      {result && (
        <div className="interpret">
          <div className="badge-row">
            <span className="badge">
              {result.cached ? '缓存' : result.source === 'llm' ? 'LLM' : 'Metadata'}
            </span>
            <span className="muted">
              表：{result.selected_tables.join(', ')}
            </span>
            {result.updated_at && (
              <span className="muted small">
                更新于 {new Date(result.updated_at).toLocaleString()}
              </span>
            )}
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
        <button type="button" className="btn ghost" onClick={onBack} disabled={busy}>
          上一步
        </button>
        <div className="actions">
          <button
            type="button"
            className="btn ghost"
            disabled={busy}
            onClick={reanalyze}
          >
            重新用大模型分析
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
      </div>
    </section>
  )
}
