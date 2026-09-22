import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import type { Connection, Dialect } from '../types'

type Props = {
  onConnected: (connection: Connection) => void
}

const DIALECTS: Array<{ value: Dialect; label: string; hint: string }> = [
  { value: 'sqlite', label: 'SQLite', hint: '本地文件库，适合演示' },
  { value: 'mysql', label: 'MySQL', hint: 'host / port / database' },
  { value: 'postgresql', label: 'PostgreSQL', hint: 'host / port / database' },
  { value: 'sqlserver', label: 'SQL Server', hint: '需本机 ODBC 驱动' },
]

export function ConnectStep({ onConnected }: Props) {
  const [existing, setExisting] = useState<Connection[]>([])
  const [dialect, setDialect] = useState<Dialect>('sqlite')
  const [name, setName] = useState('my-db')
  const [host, setHost] = useState('127.0.0.1')
  const [port, setPort] = useState(3306)
  const [database, setDatabase] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [sqlitePath, setSqlitePath] = useState('demo.db')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listConnections().then(setExisting).catch(() => setExisting([]))
  }, [])

  useEffect(() => {
    if (dialect === 'mysql') setPort(3306)
    if (dialect === 'postgresql') setPort(5432)
    if (dialect === 'sqlserver') setPort(1433)
  }, [dialect])

  async function useExisting(conn: Connection) {
    setBusy(true)
    setError(null)
    try {
      const result = await api.testConnection(conn.id)
      if (!result.ok) throw new Error(friendlyError(result.message, '连接测试失败'))
      setMessage(`已连接：${conn.name} (${result.server_version || conn.dialect})`)
      onConnected(conn)
    } catch (e) {
      setError(friendlyError(e))
    } finally {
      setBusy(false)
    }
  }

  async function removeConnection(conn: Connection) {
    if (!window.confirm(`确定删除连接「${conn.name}」吗？`)) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteConnection(conn.id)
      setExisting((prev) => prev.filter((c) => c.id !== conn.id))
      setMessage(`已删除连接：${conn.name}`)
    } catch (e) {
      setError(friendlyError(e))
    } finally {
      setBusy(false)
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const body =
        dialect === 'sqlite'
          ? {
              name,
              dialect,
              database: sqlitePath,
              options: { path: sqlitePath },
            }
          : {
              name,
              dialect,
              host,
              port,
              database,
              username,
              password,
              options:
                dialect === 'sqlserver'
                  ? { driver: 'ODBC Driver 18 for SQL Server' }
                  : {},
            }

      const created = await api.createConnection(body)
      const result = await api.testConnection(created.id)
      if (!result.ok) throw new Error(friendlyError(result.message, '连接测试失败'))
      setExisting((prev) => [...prev, created])
      setMessage(`连接成功：${result.server_version || dialect}`)
      onConnected(created)
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <header className="panel-header">
        <h2>选择数据库并连接</h2>
        <p>先选引擎类型，填好连接信息并测试通过后进入选表。</p>
      </header>

      {existing.length > 0 && (
        <div className="existing">
          <div className="toolbar">
            <h3>已有连接（{existing.length}）</h3>
          </div>
          <ul className="conn-list">
            {existing.map((c) => (
              <li key={c.id}>
                <div>
                  <strong>{c.name}</strong>
                  <span className="muted"> · {c.dialect}</span>
                </div>
                <div className="actions">
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={busy}
                    onClick={() => useExisting(c)}
                  >
                    使用
                  </button>
                  <button
                    type="button"
                    className="btn danger-ghost"
                    disabled={busy}
                    onClick={() => removeConnection(c)}
                  >
                    删除
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form className="form" onSubmit={onSubmit}>
        <div className="dialect-grid">
          {DIALECTS.map((d) => (
            <button
              key={d.value}
              type="button"
              className={`dialect-card ${dialect === d.value ? 'active' : ''}`}
              onClick={() => setDialect(d.value)}
            >
              <strong>{d.label}</strong>
              <span>{d.hint}</span>
            </button>
          ))}
        </div>

        <label>
          连接名称
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>

        {dialect === 'sqlite' ? (
          <label>
            数据库文件路径
            <input
              value={sqlitePath}
              onChange={(e) => setSqlitePath(e.target.value)}
              placeholder="demo.db"
              required
            />
          </label>
        ) : (
          <>
            <div className="row">
              <label>
                Host
                <input value={host} onChange={(e) => setHost(e.target.value)} required />
              </label>
              <label>
                Port
                <input
                  type="number"
                  value={port}
                  onChange={(e) => setPort(Number(e.target.value))}
                  required
                />
              </label>
            </div>
            <label>
              Database
              <input
                value={database}
                onChange={(e) => setDatabase(e.target.value)}
                required
              />
            </label>
            <div className="row">
              <label>
                Username
                <input value={username} onChange={(e) => setUsername(e.target.value)} />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            </div>
          </>
        )}

        <div className="actions">
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? '连接中…' : '创建并测试连接'}
          </button>
        </div>
      </form>

      {message && <p className="ok">{message}</p>}
      {error && <p className="err">{error}</p>}
    </section>
  )
}
