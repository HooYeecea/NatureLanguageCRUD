import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import type { Settings } from '../types'

type Props = {
  open: boolean
  onClose: () => void
  onSaved?: (settings: Settings) => void
}

const BASE_URL_PRESETS: Array<{ label: string; value: string }> = [
  { label: 'DeepSeek', value: 'https://api.deepseek.com' },
  { label: 'OpenAI', value: 'https://api.openai.com/v1' },
  { label: 'Moonshot', value: 'https://api.moonshot.cn/v1' },
  { label: '通义千问', value: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: 'Ollama', value: 'http://127.0.0.1:11434/v1' },
]

const MODEL_PRESETS: Array<{ label: string; value: string }> = [
  { label: 'deepseek-chat', value: 'deepseek-chat' },
  { label: 'deepseek-reasoner', value: 'deepseek-reasoner' },
  { label: 'gpt-4o', value: 'gpt-4o' },
  { label: 'gpt-4o-mini', value: 'gpt-4o-mini' },
  { label: 'moonshot-v1-8k', value: 'moonshot-v1-8k' },
  { label: 'qwen-plus', value: 'qwen-plus' },
  { label: 'qwen-turbo', value: 'qwen-turbo' },
  { label: 'llama3.1', value: 'llama3.1' },
]

const CUSTOM = '__custom__'

export function SettingsModal({ open, onClose, onSaved }: Props) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com')
  const [model, setModel] = useState('deepseek-chat')
  const [baseUrlPreset, setBaseUrlPreset] = useState('https://api.deepseek.com')
  const [modelPreset, setModelPreset] = useState('deepseek-chat')
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
        const url = s.llm_base_url || 'https://api.deepseek.com'
        const mdl = s.llm_model || 'deepseek-chat'
        setBaseUrl(url)
        setModel(mdl)
        setBaseUrlPreset(
          BASE_URL_PRESETS.some((p) => p.value === url) ? url : CUSTOM,
        )
        setModelPreset(
          MODEL_PRESETS.some((p) => p.value === mdl) ? mdl : CUSTOM,
        )
      })
      .catch((e) => setError(friendlyError(e)))
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  function onBaseUrlPresetChange(value: string) {
    setBaseUrlPreset(value)
    if (value !== CUSTOM) setBaseUrl(value)
  }

  function onModelPresetChange(value: string) {
    setModelPreset(value)
    if (value !== CUSTOM) setModel(value)
  }

  function onBaseUrlInput(value: string) {
    setBaseUrl(value)
    setBaseUrlPreset(
      BASE_URL_PRESETS.some((p) => p.value === value) ? value : CUSTOM,
    )
  }

  function onModelInput(value: string) {
    setModel(value)
    setModelPreset(
      MODEL_PRESETS.some((p) => p.value === value) ? value : CUSTOM,
    )
  }

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
      setError(friendlyError(err))
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
      setError(friendlyError(err))
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

            <div className="combo-field">
              <span className="combo-label">Base URL</span>
              <div className="combo-row">
                <div className="select-shell">
                  <select
                    className="select-control"
                    value={baseUrlPreset}
                    onChange={(e) => onBaseUrlPresetChange(e.target.value)}
                    aria-label="选择常用 Base URL"
                  >
                    {BASE_URL_PRESETS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                    <option value={CUSTOM}>自定义</option>
                  </select>
                </div>
                <input
                  value={baseUrl}
                  onChange={(e) => onBaseUrlInput(e.target.value)}
                  placeholder="可选手动输入完整地址"
                  required
                />
              </div>
            </div>

            <div className="combo-field">
              <span className="combo-label">Model</span>
              <div className="combo-row">
                <div className="select-shell">
                  <select
                    className="select-control"
                    value={modelPreset}
                    onChange={(e) => onModelPresetChange(e.target.value)}
                    aria-label="选择常用 Model"
                  >
                    {MODEL_PRESETS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                    <option value={CUSTOM}>自定义</option>
                  </select>
                </div>
                <input
                  value={model}
                  onChange={(e) => onModelInput(e.target.value)}
                  placeholder="可选手动输入模型名"
                  required
                />
              </div>
            </div>

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
