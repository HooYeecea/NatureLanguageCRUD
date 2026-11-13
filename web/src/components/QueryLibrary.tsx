import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { QueryTemplate, TemplateParameter } from '../types'

export function QueryLibrary({connectionId, draftPrompt, disabled, onRun}: {
  connectionId: string; draftPrompt: string; disabled: boolean
  onRun: (template: QueryTemplate, values: Record<string,string>) => Promise<void>
}) {
  const [items, setItems] = useState<QueryTemplate[]>([])
  const [name, setName] = useState('')
  const [editing, setEditing] = useState<string>()
  const [prompt, setPrompt] = useState('')
  const [parameters, setParameters] = useState<TemplateParameter[]>([])
  const [selected, setSelected] = useState<QueryTemplate>()
  const [values, setValues] = useState<Record<string,string>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const reload = useCallback(async () => {setItems((await api.templates(connectionId)).items)}, [connectionId])
  useEffect(() => {void reload().catch(e => setError(String(e)))}, [reload])
  useEffect(() => {if(draftPrompt) {setPrompt(draftPrompt); setEditing(undefined); setOpen(true)}}, [draftPrompt])
  useEffect(() => {
    const names = [...new Set([...prompt.matchAll(/\{\{([a-zA-Z][a-zA-Z0-9_]{0,31})\}\}/g)].map(m => m[1]))]
    setParameters(prev => names.map(name => prev.find(p => p.name === name) || {name, label: name, kind: 'text'}))
  }, [prompt])
  async function save() {
    setSaving(true); setError('')
    try {await api.saveTemplate(connectionId, {name, prompt, parameters}, editing); setEditing(undefined); setSelected(undefined); setName(''); setPrompt(''); await reload()}
    catch(e) {setError(String(e))} finally {setSaving(false)}
  }
  return <details className="feature-library" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>常用查询与参数模板 · {items.length} 项</summary>
    <p className="muted small">收藏保存在服务端，按当前凭证身份及连接区分。仅保存问题，不保存结果；每次运行都会重新生成并校验查询权限。</p>
    {error && <p role="alert">{error}</p>}
    {items.map(t => <div key={t.id} className="feature-entry"><strong>{t.name}</strong><p>{t.prompt}</p><div className="result-toolbar">
      <button type="button" className="btn ghost" disabled={disabled || saving} onClick={() => {setSelected(t); setValues({})}}>选择运行</button>
      <button type="button" className="btn ghost" disabled={disabled || saving} onClick={() => {setEditing(t.id); setName(t.name); setPrompt(t.prompt); setParameters(t.parameters)}}>编辑</button>
      <button type="button" className="btn ghost" disabled={disabled || saving} onClick={async () => {
        if(!window.confirm('删除此收藏？')) return
        setSaving(true)
        try {await api.deleteTemplate(connectionId,t.id); if(selected?.id === t.id) setSelected(undefined); await reload()}
        catch(e) {setError(String(e))} finally {setSaving(false)}
      }}>删除</button>
    </div></div>)}
    {selected && <div className="feature-form"><strong>运行 {selected.name}</strong>
      {selected.parameters.map(p => <label key={p.name}>{p.label}<input type={p.kind === 'text' ? 'text' : p.kind} maxLength={500} value={values[p.name] || ''} onChange={e => setValues({...values,[p.name]: e.target.value})}/></label>)}
      <button type="button" className="btn primary" disabled={disabled || selected.parameters.some(p => !values[p.name]?.trim())} onClick={() => void onRun(selected,values)}>运行查询</button>
    </div>}
    <div className="feature-form"><label>收藏名称<input maxLength={80} value={name} onChange={e => setName(e.target.value)}/></label>
      <label>查询问题<textarea maxLength={12000} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="统计从 {{start_date}} 到 {{end_date}} 的订单总额"/></label>
      <p className="muted small">不需要参数时直接保存问题。参数用双花括号包裹英文字母开头的名称，最多 12 项。</p>
      {parameters.map((p,i) => <div key={p.name} className="result-toolbar"><span>{p.name}</span>
        <input aria-label={p.name + ' 显示名称'} maxLength={80} value={p.label} onChange={e => setParameters(prev => prev.map((q,j) => j===i ? {...q,label:e.target.value} : q))}/>
        <select aria-label={p.name + ' 类型'} value={p.kind} onChange={e => setParameters(prev => prev.map((q,j) => j===i ? {...q,kind:e.target.value as TemplateParameter['kind']} : q))}><option value="text">文本</option><option value="date">日期</option><option value="number">数字</option></select>
      </div>)}
      <button type="button" className="btn primary" disabled={saving || !name.trim() || !prompt.trim() || parameters.length>12} onClick={() => void save()}>{editing ? '保存修改' : '保存收藏'}</button>
      {editing && <button type="button" className="btn ghost" onClick={() => {setEditing(undefined); setName(''); setPrompt('')}}>取消编辑</button>}
    </div>
  </details>
}
