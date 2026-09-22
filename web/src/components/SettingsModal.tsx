import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import type { Settings } from '../types'

type Props = {
  open: boolean
  onClose: () => void
  onSaved?: (settings: Settings) => void
}

export function SettingsModal({ open, onClose, onSaved }: Props) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com')
  const [model, setModel] = useState('deepseek-chat')
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    setOk(null)
    setApiKey('')
    api
      .settings()
      .then((s) => {
        setSettings(s)
        setBaseUrl(s.llm_base_url || 'https://api.deepseek.com')
        setModel(s.llm_model || 'deepseek-chat')
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    setOk(null)
    try {
      const saved = await api.updateSettings({
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim(),
        model: model.trim(),
      })
      setSettings(saved)
      setApiKey('')
      setOk('已保存。API Key 仅加密存储，不会明文回显。')
      onSaved?.(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function clearKey() {
    setSaving(true)
    setError(null)
    try {
      const saved = await api.updateSettings({ clear_api_key: true })
      setSettings(saved)
      setOk('已清除界面保存的 API Key（若 .env 仍有值，会继续使用环境变量）。')
      onSaved?.(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <h2 id="settings-title">API 设置</h2>
            <p className="muted">配置大模型 API，用于表关系解读与自然语言查写。</p>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>
            关闭
          </button>
        </header>

        {loading ? (
          <p className="muted">加载中…</p>
        ) : (
          <form className="form" onSubmit={onSubmit}>
            <div className="settings-status">
              <span className={`badge ${settings?.llm_configured ? 'ok' : ''}`}>
                {settings?.llm_configured ? '已配置' : '未配置'}
              </span>
              {settings?.api_key_masked && (
                <span className="muted small">当前 Key：{settings.api_key_masked}</span>
              )}
              {settings?.source && (
                <span className="muted small">来源：{settings.source}</span>
              )}
            </div>

            <label>
              API Key
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={
                  settings?.llm_configured
                    ? '留空则保持现有 Key 不变'
                    : '例如 sk-...'
                }
                autoComplete="off"
              />
            </label>

            <label>
              Base URL
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.deepseek.com"
                required
              />
            </label>

            <label>
              Model
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="deepseek-chat"
                required
              />
            </label>

            <div className="actions spread">
              <button
                type="button"
                className="btn ghost"
                disabled={saving || !settings?.llm_configured}
                onClick={clearKey}
              >
                清除 Key
              </button>
              <button type="submit" className="btn primary" disabled={saving}>
                {saving ? '保存中…' : '保存'}
              </button>
            </div>
          </form>
        )}

        {ok && <p className="ok">{ok}</p>}
        {error && <p className="err">{error}</p>}
      </div>
    </div>
  )
}
